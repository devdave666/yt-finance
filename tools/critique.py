"""Gemini multimodal critique of a rendered short -- what's weak, what to fix.

    python tools/critique.py build/<slug>/<slug>.mp4
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402
from stickfin import config  # noqa: E402

MODELS = [("us-central1", "gemini-2.5-pro"), ("us-central1", "gemini-2.5-flash")]

SYSTEM = (
    "You are a blunt short-form video editor reviewing an AI-generated "
    "stick-figure finance Short before it goes on YouTube. The pipeline: one "
    "generated background, transparent stick-figure pose cutouts composited as "
    "layers, hard cuts on the narration beat, burned captions, TTS voice. Judge "
    "it as a viewer scrolling, and as someone who has to fix the pipeline."
)

PROMPT = """Watch this vertical Short and critique it. Return ONLY JSON:

{
  "first_impression": "one honest sentence -- would this stop a scroll?",
  "scores": { "hook": 0-10, "visual_consistency": 0-10, "animation_feel": 0-10, "captions": 0-10, "audio_pacing": 0-10, "overall": 0-10 },
  "whats_broken": [ { "issue": "...", "where": "timestamp or 'throughout'", "severity": "high|medium|low", "fix": "concrete pipeline change" } ],
  "whats_weak_but_ok": [ "..." ],
  "character": "is the stick figure consistent shot to shot? any stray facial hair, proportion drift, colour shifts, furniture stuck to the cutout, bad matte edges?",
  "composition": "are figures/props well placed, sized, not overlapping badly, not clipping the caption or frame edge?",
  "quick_wins": [ "the 3-5 highest-leverage fixes, ranked" ]
}

No prose outside the JSON."""


def main(path: str) -> None:
    data = Path(path).read_bytes()
    print(f"{path} ({len(data)/1e6:.1f} MB)")
    last = None
    for loc, model in MODELS:
        try:
            client = genai.Client(vertexai=True, project=config.GCP_PROJECT, location=loc)
            resp = client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=data, mime_type="video/mp4"), PROMPT],
                config=types.GenerateContentConfig(system_instruction=SYSTEM,
                                                   response_mime_type="application/json"),
            )
            out = Path(path).with_suffix(".critique.json")
            out.write_text(resp.text)
            print(f"[{model}@{loc}] -> {out}\n")
            print(json.dumps(json.loads(resp.text), indent=2))
            return
        except Exception as e:  # noqa: BLE001
            print(f"[{model}@{loc}] {str(e)[:200]}")
            last = e
    raise SystemExit(f"critique failed: {last}")


if __name__ == "__main__":
    main(sys.argv[1])
