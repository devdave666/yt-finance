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
            "silenceremove=stop_periods=-1:stop_duration=0.30:stop_threshold=-40dB:"
            "detection=peak")
    if extra_af:
        chain.append(extra_af)
    chain.append(f"loudnorm=I={config.TTS_TARGET_LUFS}:TP=-1.5:LRA=11")
    chain.append(f"apad=pad_dur={config.BEAT_GAP_S}")
    run_ffmpeg(["-i", src, "-af", ",".join(chain),
                "-ar", config.TTS_SAMPLE_RATE, "-ac", "1", dst],
               f"normalize {dst.name}")


def _enforce_pace(path: Path, n_words: int) -> None:
    """If a beat came back slower than TTS_MIN_WPS, speed it up with atempo
    (pitch-preserving). Gemini-TTS ignores the rate hint on some lines and
    drags -- this is the hard floor."""
    floor = getattr(config, "TTS_MIN_WPS", 0)
    if floor <= 0 or n_words < 2:
        return
    speech = max(probe_duration(path) - config.BEAT_GAP_S, 0.1)
    wps = n_words / speech
    if wps >= floor:
        return
    # a normal slow line just needs a nudge; wps below ~1.5 means the synth
    # itself is broken (huge internal drag) -- allow a bigger stretch there.
    cap = 1.4 if wps > 1.5 else 2.0
    factor = min(cap, round(floor / wps, 3))
    tmp = path.with_suffix(".pace.wav")
    run_ffmpeg(["-i", path, "-af", f"atempo={factor}",
                "-ar", config.TTS_SAMPLE_RATE, "-ac", "1", tmp],
               f"speed up {path.name} x{factor} ({wps:.1f}->{floor} wps)")
    tmp.replace(path)


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

    client = None
    entries = []
    for i, beat in enumerate(script.beats):
        final = audio_dir / f"{beat.id}.wav"

        if force or not final.exists():
            raw = audio_dir / f"{beat.id}.raw.wav"
            if beat.is_live:
                lo, hi = _parse_trim(beat.live["trim"])
                seg = ["-ss", str(lo), "-i", beat.live["src"], "-vn"]
                if hi is not None:
                    seg += ["-t", str(hi - lo)]
                run_ffmpeg(seg + [raw], f"live audio {beat.id}")
            else:
                if client is None:
                    client = _client()
                hint = (" This is the opening hook -- punch it, make them stop "
                        "scrolling." if i == 0 else "")
                _synth_raw(client, beat.say, script.voice_for(beat), raw,
                           style=config.TTS_STYLE + hint)
            _norm(raw, final)
            if not beat.is_live:
                _enforce_pace(final, len(beat.say.split()))
            raw.unlink(missing_ok=True)

        dur = round(probe_duration(final), 3)
        if beat.is_live or not beat.say:
            s0, s1 = 0.0, dur
        else:
            s0, s1 = _speech_span(final, dur)
        entries.append({"id": beat.id, "wav": str(final), "duration_s": dur,
                        "speech_start_s": s0, "speech_end_s": s1})

    listfile = audio_dir / "_concat.txt"
    listfile.write_text(
        "".join(f"file '{Path(e['wav']).resolve().as_posix()}'\n" for e in entries),
        encoding="utf-8")
    run_ffmpeg(["-f", "concat", "-safe", "0", "-i", listfile,
                "-ar", config.TTS_SAMPLE_RATE, "-ac", "1",
                script.build_dir / "voiceover.wav"], "concat voiceover")
    listfile.unlink(missing_ok=True)

    manifest = {"total_s": round(sum(e["duration_s"] for e in entries), 3),
                "beats": entries}
    (script.build_dir / "narration.json").write_text(json.dumps(manifest, indent=2))
    return manifest
