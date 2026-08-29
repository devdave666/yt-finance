"""Gemini multimodal analysis of reference reels -> refs/<name>.analysis.json

Whole-video analysis (not frame sampling). Same client pattern as everything
else: genai.Client(vertexai=True). Videos are sent inline as bytes, so keep
them under ~15MB each.

    python tools/analyze_refs.py C:/Users/Dev/Downloads/s_ref.mp4 [more.mp4 ...]
"""
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402
from stickfin import config  # noqa: E402

MODELS = [
    ("us-central1", "gemini-2.5-pro"),
    ("global", "gemini-3.6-flash"),
    ("global", "gemini-3.1-flash"),
    ("us-central1", "gemini-2.5-flash"),
]

SYSTEM = (
    "You are an animation director who reverse-engineers stick-figure explainer "
    "and skit videos so they can be reproduced with an image model plus ffmpeg. "
    "Be concrete about line weight, fill, faces, motion technique and timing -- "
    "no vague adjectives without the visual detail behind them."
)

PROMPT = """Analyse this vertical video and reverse-engineer how to remake its ASSETS and VIBE.

Return ONLY one JSON object:
- "one_line": what this video is, in a sentence
- "aspect": "9:16" | "16:9" | "1:1"
- "character": detailed spec of the figure(s) -- outline weight, fill colour, head shape, face (eyes/mouth style + how expressions are shown), body, clothing/props, how consistent it stays
- "background": how scenes are built -- flat white? a drawn environment? photoreal cutouts pasted in? colour palette; list recurring set pieces
- "assets_needed": array of the distinct reusable asset types someone would have to generate to make a video like this (e.g. "white stick figure turnaround", "judge robe pose set", "photoreal yacht PNG cutout", "simple flat office background")
- "motion_technique": exactly how movement is done -- frame-by-frame? held poses with swaps? how many drawings per action? mouth flaps? tweened slides? camera moves? be specific
- "hold_time": typical seconds a single drawing stays on screen before something changes, as a range
- "cut_rhythm": how often the visible frame changes (pose swap OR caption OR cut), in seconds
- "captions": position, case, font character, colour, background pill, timing (per-phrase vs persistent), any emoji
- "audio": voiceover character (TTS? real? one voice or several?), music, sfx
- "structure": array of {t, beat} for the whole video
- "remake_notes": 3-6 concrete instructions for reproducing this style with an image model + ffmpeg pipeline (what to prompt for, what to composite, what cadence to cut at)

No prose outside the JSON."""


def _extract_json(text: str):
    m = re.search(r"\{.*\}", text, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def analyse(video: Path) -> None:
    data = video.read_bytes()
    print(f"\n### {video.name} ({len(data) / 1e6:.1f} MB)")
    last_err = None
    for loc, model in MODELS:
        try:
            client = genai.Client(vertexai=True, project=config.GCP_PROJECT, location=loc)
            resp = client.models.generate_content(
                model=model,
                contents=[types.Part.from_bytes(data=data, mime_type="video/mp4"), PROMPT],
                config=types.GenerateContentConfig(system_instruction=SYSTEM),
            )
            parsed = _extract_json(resp.text or "")
            if parsed:
                out = Path("refs") / f"{video.stem}.analysis.json"
                out.write_text(json.dumps(parsed, indent=2))
                print(f"  [{model}@{loc}] -> {out}")
                print(json.dumps(parsed, indent=2)[:2400])
                return
            print(f"  [{model}@{loc}] no JSON in reply, trying next")
        except Exception as e:  # noqa: BLE001
            print(f"  [{model}@{loc}] {str(e)[:200]}")
            last_err = e
    print(f"  FAILED: {last_err}")


if __name__ == "__main__":
    Path("refs").mkdir(exist_ok=True)
    for arg in sys.argv[1:]:
        analyse(Path(arg))
