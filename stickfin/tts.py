"""Per-beat audio.

Spoken beats -> Google Cloud TTS, one clip each, in that beat's character
voice (so a two-hander skit gets two voices). Live beats -> the audio sliced
out of the source clip. Either way each beat clip's length is that beat's
screen time, and narration.json records it. No Speech-to-Text, no alignment.

Auth: ADC. Enable the API once:
    gcloud services enable texttospeech.googleapis.com --project <PROJECT>
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

from . import config
from .ffmpeg_util import probe_duration, run_ffmpeg


def _client():
    from google.cloud import texttospeech
    return texttospeech.TextToSpeechClient()


def _is_ssml_voice(voice: str) -> bool:
    low = voice.lower()
    return not any(k in low for k in ("chirp", "journey", "studio", "casual"))


def _esc(t: str) -> str:
    return t.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _synth_raw(client, text: str, voice: str, out_raw: Path, style: str = "") -> None:
    from google.cloud import texttospeech

    model = config.TTS_MODEL
    if model.startswith("gemini"):
        # Gemini-TTS wants a bare roster name (Charon, Aoede, ...). Map any
        # legacy "en-US-Chirp3-HD-Charon" style id down to its last segment.
        gem_voice = voice.split("-")[-1] if "-" in voice else voice
        # Gemini-TTS: plain text + a natural-language delivery prompt
        payload = texttospeech.SynthesisInput(
            text=text, prompt=(style or config.TTS_STYLE))
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=config.TTS_LANGUAGE, name=gem_voice, model_name=model)
    elif _is_ssml_voice(voice):
        payload = texttospeech.SynthesisInput(
            ssml=f'<speak><break time="50ms"/>{_esc(text)}<break time="110ms"/></speak>')
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=config.TTS_LANGUAGE, name=voice)
    else:
        payload = texttospeech.SynthesisInput(text=text)
        voice_params = texttospeech.VoiceSelectionParams(
            language_code=config.TTS_LANGUAGE, name=voice)

    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=config.TTS_SAMPLE_RATE,
        speaking_rate=config.TTS_SPEAKING_RATE)
    req = texttospeech.SynthesizeSpeechRequest(
        input=payload, voice=voice_params, audio_config=audio_config)

    import time
    from google.api_core import exceptions as gexc
    for attempt in range(6):
        try:
            resp = client.synthesize_speech(request=req)
            out_raw.write_bytes(resp.audio_content)
            return
        except gexc.ResourceExhausted:
            if attempt == 5:
                raise
            wait = 15 * (2 ** attempt)
            print(f"    TTS 429 -- retrying in {wait}s ({attempt + 1}/6)")
            time.sleep(wait)


def _parse_trim(trim: str) -> tuple[float, float | None]:
    def to_s(x: str) -> float:
        x = x.strip()
        if ":" in x:
            m, s = x.split(":")
            return int(m) * 60 + float(s)
        return float(x)
    if not trim:
        return 0.0, None
    lo, _, hi = trim.partition("-")
    return to_s(lo), (to_s(hi) if hi else None)


def _norm(src: Path, dst: Path, extra_af: str = "") -> None:
    chain = []
    if getattr(config, "TTS_TRIM_SILENCE", False):
        # 1) trim dead air off both ends, 2) collapse any internal pause longer
        # than ~0.3s down to 0.3s -- Gemini-TTS sometimes drops a multi-second
        # gap mid-line (a 7-word closer once came back 22s long).
        chain.append(
            "silenceremove=start_periods=1:start_silence=0.04:start_threshold=-42dB:"
            "detection=peak,areverse,"
            "silenceremove=start_periods=1:start_silence=0.04:start_threshold=-42dB:"
            "detection=peak,areverse,"
            "silenceremove=stop_periods=-1:stop_duration=0.55:stop_threshold=-40dB:"
            "detection=peak")
    if extra_af:
        chain.append(extra_af)
    chain.append(f"loudnorm=I={config.TTS_TARGET_LUFS}:TP=-1.5:LRA=11")
    chain.append(f"apad=pad_dur={config.BEAT_GAP_S}")
    run_ffmpeg(["-i", src, "-af", ",".join(chain),
                "-ar", config.TTS_SAMPLE_RATE, "-ac", "1", dst],
               f"normalize {dst.name}")


def _wps(path: Path, n_words: int) -> float:
    speech = max(probe_duration(path) - config.BEAT_GAP_S, 0.1)
    return n_words / speech


def _polish_pace(path: Path, n_words: int) -> float:
    """Bring a beat to TTS_TARGET_WPS with a pitch-preserving atempo (0.85x-1.28x).
    Gemini-TTS delivers this voice slow, so this is normally a ~1.2x speed-up."""
    if n_words < 2:
        return _wps(path, n_words)
    target = config.TTS_TARGET_WPS
    wps = _wps(path, n_words)
    factor = target / wps
    if 0.97 <= factor <= 1.03:
        return wps
    # Gemini-TTS runs slow with this voice, so the usual move is a ~1.2x speed-up;
    # verified clean by ear + critique. Cap at 1.28x (past that atempo warbles);
    # a slower raw take just lands a little under target, which is fine.
    factor = min(1.28, max(0.85, round(factor, 3)))
    tmp = path.with_suffix(".pace.wav")
    run_ffmpeg(["-i", path, "-af", f"atempo={factor}",
                "-ar", config.TTS_SAMPLE_RATE, "-ac", "1", tmp],
               f"polish pace {path.name} x{factor} ({wps:.2f} wps)")
    tmp.replace(path)
    return _wps(path, n_words)


def _speech_span(path: Path, total: float) -> tuple[float, float]:
    """(start, end) seconds of actual speech within a beat clip.

    Captions key off this so the karaoke highlight starts exactly when the
    voice starts, not when the clip starts -- lead-in room tone that's too
    faint for the trim filter to catch was pushing the highlight ahead of the
    speech. Falls back to the whole clip if detection is unclear.
    """
    try:
        r = subprocess.run(
            ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
             "-af", "silencedetect=noise=-38dB:d=0.12", "-f", "null", "-"],
            capture_output=True, text=True)
        log = r.stderr
        starts = [float(x) for x in re.findall(r"silence_start:\s*(-?[0-9.]+)", log)]
        ends = [float(x) for x in re.findall(r"silence_end:\s*([0-9.]+)", log)]
        start = ends[0] if starts and starts[0] <= 0.06 and ends else 0.0
        end = total
        if len(starts) > len(ends) and starts[-1] > start:      # trailing silence to EOF
            end = starts[-1]
        elif ends and starts and starts[-1] > start and ends[-1] >= total - 0.03:
            end = starts[-1]
        if 0.0 <= start < end <= total + 0.05 and end - start >= 0.2:
            return round(start, 3), round(min(end, total), 3)
    except Exception as e:  # noqa: BLE001
        print(f"    (speech-span probe failed for {path.name}: {e})")
    return 0.0, round(total, 3)


def synthesize(script, force: bool = False) -> dict:
    audio_dir = script.build_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)

    lo_wps, hi_wps = config.TTS_WPS_BAND
    client = None
    entries = []
    for i, beat in enumerate(script.beats):
        final = audio_dir / f"{beat.id}.wav"
        n_words = len(beat.say.split())

        if force or not final.exists():
            raw = audio_dir / f"{beat.id}.raw.wav"
            if beat.is_live:
                lo, hi = _parse_trim(beat.live["trim"])
                seg = ["-ss", str(lo), "-i", beat.live["src"], "-vn"]
                if hi is not None:
                    seg += ["-t", str(hi - lo)]
                run_ffmpeg(seg + [raw], f"live audio {beat.id}")
                _norm(raw, final)
            else:
                if client is None:
                    client = _client()
                hint = (" This is the opening hook -- punch it, make them stop "
                        "scrolling." if i == 0 else "")
                # re-roll the take until its pace lands in band (Gemini-TTS is
                # stochastic -- a fresh take fixes a rushed/draggy line far
                # better than time-stretching one)
                best_wps = None
                for take in range(3):
                    _synth_raw(client, beat.say, script.voice_for(beat), raw,
                               style=config.TTS_STYLE + hint)
                    cand = audio_dir / f"{beat.id}.take.wav"
                    _norm(raw, cand)
                    w = _wps(cand, n_words)
                    if best_wps is None or abs(w - config.TTS_TARGET_WPS) < abs(best_wps - config.TTS_TARGET_WPS):
                        cand.replace(final)
                        best_wps = w
                    else:
                        cand.unlink(missing_ok=True)
                    if lo_wps <= w <= hi_wps:
                        break
                    if take < 2:
                        print(f"    {beat.id}: take {take + 1} was {w:.2f} wps "
                              f"(want {lo_wps}-{hi_wps}), re-rolling")
                w = _polish_pace(final, n_words)
                print(f"    {beat.id}: {w:.2f} wps")
            raw.unlink(missing_ok=True)

        dur = round(probe_duration(final), 3)
        if beat.is_live or not beat.say:
            s0, s1 = 0.0, dur
        else:
            s0, s1 = _speech_span(final, dur)
        entries.append({"id": beat.id, "wav": str(final), "duration_s": dur,
                        "speech_start_s": s0, "speech_end_s": s1,
                        "wps": round(n_words / max(s1 - s0, 0.1), 2) if n_words > 1 else None})

    listfile = audio_dir / "_concat.txt"
    listfile.write_text(
        "".join(f"file '{Path(e['wav']).resolve().as_posix()}'\n" for e in entries),
        encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", listfile,
                "-ar", config.TTS_SAMPLE_RATE, "-ac", "1",
                script.build_dir / "voiceover.wav"], "concat voiceover")
    listfile.unlink(missing_ok=True)

    spoken = [e["wps"] for e in entries if e.get("wps")]
    spread = round(max(spoken) / min(spoken), 2) if len(spoken) >= 2 else 1.0
    if spread > 1.7:
        print(f"    ! pace inconsistent across beats (spread {spread}x): "
              + ", ".join(f"{e['id']}={e['wps']}" for e in entries if e.get('wps')))

    manifest = {"total_s": round(sum(e["duration_s"] for e in entries), 3),
                "wps_spread": spread,
                "beats": entries}
    (script.build_dir / "narration.json").write_text(json.dumps(manifest, indent=2))
    return manifest
