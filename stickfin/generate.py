"""Stage 0: pick the next topic and write a full script -- no human in the loop.

Reads themes.yaml, chooses the least-recently-used topic that's outside the
cooldown window, asks Gemini (Vertex) to write a script + YouTube metadata,
validates it by loading it through script_model, and writes:

    scripts/auto/<date>-<slug>.yaml       the script
    scripts/auto/<date>-<slug>.meta.json  {title, description, tags, format}
    state/topic_history.json              appended (committed back by CI)

Voices are assigned here, not by the model, so the channel keeps a consistent
narrator.
"""
from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path

import yaml

from . import config, script_model

TEXT_MODELS = [("us-central1", "gemini-2.5-pro"), ("us-central1", "gemini-2.5-flash")]

CHANNEL_VOICE = "en-US-Neural2-D"     # the narrator / first character, every video
SECOND_VOICE = "en-US-Neural2-A"      # the other character in a skit

# The channel's recurring characters -- fixed here (not written by the model) so
# the figure looks identical across every upload. The model still chooses poses
# and expressions per beat.
HOST_LOOK = (
    "friendly stick figure, thick even black outline, round white head, two "
    "black dot eyes, small simple smile, a single wavy line for a moustache, "
    "no body fill, single-line arms and legs, small round hands"
)
SECOND_LOOK = (
    "stick figure, thick even black outline, round white head, two dot eyes, "
    "plain teal t-shirt, single-line arms and legs, small round hands"
)

STATE = Path("state/topic_history.json")
AUTO_DIR = Path("scripts/auto")

SYSTEM = """You are the head writer for a faceless personal-finance YouTube Shorts channel.
You write tight, accurate, plain-English scripts that a stick-figure animation pipeline turns into a vertical video.
You never give individualised advice and never tell viewers what to buy. You explain how money works."""

SCHEMA_DOC = """Return ONLY a JSON object, no prose, with this shape:

{
  "format": "explainer" | "skit",
  "slug": "kebab-case-topic-slug",
  "title": "YouTube title, <=70 chars, specific, no clickbait punctuation spam",
  "description": "2-3 sentence description, then a blank line, then 3-5 hashtags",
  "tags": ["personal finance", "..."],           // 5-10 short tags
  "script": {
    "title": "same as title",
    "slug": "same as slug",
    "format": "short",
    "caption_style": "explainer" for explainer format, "title" for skit format,
    "title_card": "only for skit format: the persistent meme-style caption, with one emoji",
    "cast": {
      "<name>": { "look": "detailed stick-figure description: outline weight, head, face, clothing/props", "anchor": "left"|"center"|"right" }
      // explainer: exactly ONE character named "host", anchor center
      // skit: exactly TWO characters, one anchor left and one anchor right
    },
    "scenes": {
      "<name>": { "bg": "flat 2D vector background description, no characters, no text" }
      // explainer: a single scene named "void" with { "color": "#ffffff" } instead of bg
      // skit: 1-2 drawn scenes
    },
    "beats": [
      {
        "id": "b01",
        "scene": "<scene name>",
        "who": "<character name>",       // the speaker; for explainer always "host"
        "say": "ONE spoken sentence, <= 16 words, no numbers written as digits unless a real figure",
        "cast": { "<name>": "pose and facial expression, e.g. 'standing, pointing at a bar chart, neutral'" },
        "props": ["optional simple objects drawn in the same style, e.g. 'a jar of coins'"]
      }
      // 8 to 11 beats, targeting 20-35 seconds of narration total. Every character
      // mentioned in a beat's cast must be in the top-level cast.
      // First beat is a scroll-stopping hook. Last beat is a calm one-line takeaway (no hard sell).
      // Re-use props across beats where natural (an evolving chart is the same prop name each time).
    ]
  }
}

Rules:
- Accurate. Any figure used must be roughly correct.
- No specific tickers, funds, apps, or products to buy. No promises of returns. No hype phrasing.
- explainer = one narrator ("host") explaining to camera with props/charts.
- skit = two characters, a short situation, a punchline in the final beat.
- Keep every `say` short enough to land in about 1-2 seconds of speech.
"""


def _load_themes() -> dict:
    return yaml.safe_load(Path("themes.yaml").read_text(encoding="utf-8"))


def _history() -> list[dict]:
    if STATE.exists():
        return json.loads(STATE.read_text())
    return []


def _pick_topic(themes: dict, history: list[dict]) -> str:
    topics = themes["topics"]
    cooldown = int(themes.get("cooldown", 10))
    recent = {h["topic"] for h in history[-cooldown:]}
    last_used = {}
    for i, h in enumerate(history):
        last_used[h["topic"]] = i
    eligible = [t for t in topics if t not in recent] or topics
    # least-recently-used first, then themes.yaml order
    eligible.sort(key=lambda t: (last_used.get(t, -1), topics.index(t)))
    return eligible[0]


def _ask(topic: str, themes: dict) -> dict:
    # NOTE: keep a reference to the genai Client for the whole call. If it is
    # only a throwaway in an expression (mk().models.generate_content(...)) the
    # SDK's httpx transport gets closed on GC mid-request -> "client has been
    # closed". Learned the hard way.
    from google import genai
    from google.genai import types

    prompt = (
        f"{SCHEMA_DOC}\n\n"
        f"CHANNEL NICHE:\n{themes['niche']}\n\n"
        f"NARRATION VOICE:\n{themes['voice']}\n\n"
        f"HARD RULES:\n- " + "\n- ".join(themes.get("rules", [])) + "\n\n"
        f"TODAY'S TOPIC: {topic}\n\n"
        "Write the video now. JSON only."
    )
    cfg = types.GenerateContentConfig(
        system_instruction=SYSTEM, temperature=0.9,
        response_mime_type="application/json")

    last = None
    for loc, model in TEXT_MODELS:
        try:
            client = genai.Client(vertexai=True, project=config.GCP_PROJECT, location=loc)
            resp = client.models.generate_content(model=model, contents=[prompt], config=cfg)
            return json.loads(resp.text)
        except Exception as e:  # noqa: BLE001
            print(f"  [{model}@{loc}] {str(e)[:200]}")
            last = e
    raise RuntimeError(f"script generation failed: {last}")


def _inject_identity(script_obj: dict) -> None:
    """Force the channel's fixed voices + character designs onto whatever the
    model returned, so brand identity is stable across every upload."""
    cast = script_obj.get("cast") or {}
    for i, name in enumerate(cast):
        cast[name]["voice"] = CHANNEL_VOICE if i == 0 else SECOND_VOICE
        cast[name]["look"] = HOST_LOOK if i == 0 else SECOND_LOOK
    script_obj.setdefault("narrator", {})["voice"] = CHANNEL_VOICE


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "video"


def generate(out_dir: Path | None = None, dry_topic: str | None = None) -> tuple[Path, dict]:
    themes = _load_themes()
    history = _history()
    topic = dry_topic or _pick_topic(themes, history)
    print(f"[generate] topic: {topic}")

    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    date = dt.date.today().isoformat()

    obj = None
    for attempt in range(3):
        cand = _ask(topic, themes)
        script_obj = cand["script"]
        script_obj["slug"] = f"{date}-{_slugify(cand.get('slug') or topic)}"
        script_obj["title"] = cand.get("title") or script_obj.get("title") or topic
        _inject_identity(script_obj)
        path = AUTO_DIR / f"{script_obj['slug']}.yaml"
        path.write_text(yaml.safe_dump(script_obj, sort_keys=False, allow_unicode=True),
                        encoding="utf-8")
        try:
            script_model.load_script(path)
            obj = cand
            break
        except Exception as e:  # noqa: BLE001
            print(f"  invalid script (attempt {attempt + 1}): {e}")
            path.unlink(missing_ok=True)
    if obj is None:
        raise RuntimeError(f"could not produce a valid script for: {topic}")

    meta = {
        "topic": topic,
        "format": obj.get("format"),
        "title": obj["title"] if "title" in obj else script_obj["title"],
        "description": obj.get("description", ""),
        "tags": obj.get("tags", []),
        "slug": script_obj["slug"],
        "date": date,
    }
    (AUTO_DIR / f"{script_obj['slug']}.meta.json").write_text(json.dumps(meta, indent=2))

    history.append({"date": date, "topic": topic, "slug": script_obj["slug"]})
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(history, indent=2))

    print(f"[generate] wrote {path}  ({obj.get('format')}, "
          f"{len(script_obj['beats'])} beats)")
    return path, meta


if __name__ == "__main__":
    import sys
    generate(dry_topic=sys.argv[1] if len(sys.argv) > 1 else None)
