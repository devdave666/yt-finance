"""Offline stand-in for the audio stage.

Estimates each beat's duration from word count (live beats get a fixed 3s) and
writes narration.json + a silent voiceover.wav, so plan / assets(placeholder) /
render can run with no Google Cloud calls.

    python tools/fake_narration.py scripts/skit_doctor_glasses.yaml
"""
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from stickfin import config, script_model  # noqa: E402

WORDS_PER_SEC = 2.7


def main(path: str) -> None:
    s = script_model.load_script(path)
    s.build_dir.mkdir(parents=True, exist_ok=True)

    beats, total = [], 0.0
    for b in s.beats:
        speech = 3.0 if b.is_live else max(1.2, len(b.say.split()) / WORDS_PER_SEC)
        secs = speech + config.BEAT_GAP_S
        beats.append({"id": b.id, "wav": "", "duration_s": round(secs, 3),
                      "speech_start_s": 0.0, "speech_end_s": round(speech, 3)})
        total += secs

    (s.build_dir / "narration.json").write_text(json.dumps(
        {"total_s": round(total, 3), "beats": beats}, indent=2))
    subprocess.run(
        ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", "-f", "lavfi",
         "-i", f"anullsrc=r={config.TTS_SAMPLE_RATE}:cl=mono", "-t", f"{total:.3f}",
         str(s.build_dir / "voiceover.wav")], check=True)
    print(f"fake narration: {total:.1f}s over {len(beats)} beats -> {s.build_dir}")


if __name__ == "__main__":
    main(sys.argv[1])
