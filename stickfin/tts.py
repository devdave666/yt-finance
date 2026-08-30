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
        # cut dead air off both ends -- glacial gaps between lines was the top note
        chain.append(
            "silenceremove=start_periods=1:start_silence=0.04:start_threshold=-45dB:"
            "detection=peak,areverse,"
            "silenceremove=start_periods=1:start_silence=0.04:start_threshold=-45dB:"
            "detection=peak,areverse")
    if extra_af:
        chain.append(extra_af)
    chain.append(f"loudnorm=I={config.TTS_TARGET_LUFS}:TP=-1.5:LRA=11")
    chain.append(f"apad=pad_dur={config.BEAT_GAP_S}")
    run_ffmpeg(["-i", src, "-af", ",".join(chain),
                "-ar", config.TTS_SAMPLE_RATE, "-ac", "1", dst],
               f"normalize {dst.name}")


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
                hint = ""
                if i == 0:
                    hint = (" This is the opening hook -- punch it, a little "
                            "provocative, make them stop scrolling.")
                elif i == len(script.beats) - 1:
                    hint = (" This is the closing line -- slow down and let it land.")
                _synth_raw(client, beat.say, script.voice_for(beat), raw,
                           style=config.TTS_STYLE + hint)
            _norm(raw, final)
            raw.unlink(missing_ok=True)

        entries.append({"id": beat.id, "wav": str(final),
                        "duration_s": round(probe_duration(final), 3)})

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
