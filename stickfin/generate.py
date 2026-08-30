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

from . import config, icons, script_model

TEXT_MODELS = [("us-central1", "gemini-2.5-pro"), ("us-central1", "gemini-2.5-flash")]

CHANNEL_VOICE = "Charon"     # narrator / first character (Gemini-TTS roster), every video
SECOND_VOICE = "Aoede"       # the other character in a skit

# Every explainer uses this one backdrop (generated once, reused) instead of
# stark white -- first review called the white background "unappealing".
STAGE_BG = (
    "a plain soft warm off-white backdrop with a very subtle paper texture and "
    "a gentle vignette darkening the corners slightly. Completely empty and "
    "even -- NO floor line, NO horizon line, NO ground, NO shadow, NO objects, "
    "no furniture, no text."
)

# The channel's recurring characters -- fixed here (not written by the model) so
# the figure looks identical across every upload. The model still chooses poses
# and expressions per beat.
HOST_LOOK = (
    "a classic minimalist stick figure drawn with a black marker: a round OPEN "
    "white head (thin black outline, two small dot eyes, tiny smile, "
    "clean-shaven, no facial hair), then ONE single straight vertical black "
    "line for the entire body/spine, plus four more single straight black lines "
    "for the two arms and two legs, ending in tiny round dot hands and short "
    "line feet. The whole figure is nothing but a head outline and five thin "
    "lines. It has NO torso shape, NO filled body, NO solid black wedge, NO "
    "clothing -- it is never a black silhouette."
)
SECOND_LOOK = (
    "a classic minimalist stick figure like the host but drawn slightly shorter, "
    "same construction: round open white head, ONE straight vertical line for "
    "the spine, single straight lines for arms and legs, dot hands. No torso "
    "shape, no fill, no silhouette. A small plain teal collar is the only "
    "difference from the host."
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
      // explainer: a single scene named "stage" -- OMIT its bg, the pipeline fills it
      // skit: 1-2 drawn scenes
    },
    "beats": [
      {
        "id": "b01",
        "scene": "<scene name>",
        "who": "<character name>",       // the speaker; for explainer always "host"
        "say": "ONE short spoken sentence, <= 10 words, punchy",
        "cast": { "<name>": "pose and facial expression, e.g. 'standing, pointing to the right, neutral'" },
        "props": ["at most ONE prop per beat, chosen ONLY from the PROP VOCAB below (exact name), or omit"]
      }
      // 6 to 8 beats, targeting 20-28 seconds of narration total. Every character
      // mentioned in a beat's cast must be in the top-level cast.
      // Beat 1 MUST be a scroll-stopping hook: a pointed question, a surprising
      //   number, or a "you're doing X wrong" -- NEVER a definition or "Let me explain".
      // Last beat is a calm one-line takeaway (no hard sell).
      // At most one prop per beat. Reuse the SAME prop name across beats when it's
      //   the same object evolving. Props must be concrete and instantly readable
      //   (a piggy bank, a padlock, a rising line chart) -- not abstract.
    ]
  }
}

Rules:
- Accurate. Any figure used must be roughly correct.
- No specific tickers, funds, apps, or products to buy. No promises of returns. No hype phrasing.
- explainer = one narrator ("host") explaining to camera; keep gestures simple ("pointing to the right", "shrugging", "arms open").
- skit = two characters, a short situation, a punchline in the final beat.
- Every `say` must land in about 1-2 seconds of speech. Short. Punchy. Spoken, not written.
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

    vocab = ", ".join(icons.names()) or "(none available -- use no props)"
    prompt = (
        f"{SCHEMA_DOC}\n\n"
        f"PROP VOCAB (use these exact names, nothing else):\n{vocab}\n\n"
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
    """Force the channel's fixed voices, character designs and backdrop onto
    whatever the model returned, so brand identity is stable across uploads."""
    cast = script_obj.get("cast") or {}
    for i, name in enumerate(cast):
        cast[name]["voice"] = CHANNEL_VOICE if i == 0 else SECOND_VOICE
        cast[name]["look"] = HOST_LOOK if i == 0 else SECOND_LOOK
    script_obj.setdefault("narrator", {})["voice"] = CHANNEL_VOICE

    if script_obj.get("caption_style") == "explainer":
        scenes = script_obj.setdefault("scenes", {})
        if not scenes:
            scenes["stage"] = {}
        for name in scenes:
            scenes[name] = {"bg": STAGE_BG}      # one consistent backdrop
        only = next(iter(scenes))
        for beat in script_obj.get("beats", []):
            beat["scene"] = only

    # drop any prop the model invented that isn't in the committed icon library
    allowed = set(icons.names())
    if allowed:
        for beat in script_obj.get("beats", []):
            beat["props"] = [p for p in (beat.get("props") or []) if p in allowed][:1]


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
