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

CHANNEL_VOICE = "Orus"       # narrator / first character (Gemini-TTS roster), every video
SECOND_VOICE = "Aoede"       # the other character in a skit

# Every explainer uses this one flat warm backdrop (compositor adds a soft
# vignette + grain). Not an image-model generation -- it kept drawing a framed
# border / rectangle around the "backdrop".
STAGE_COLOR = "#f4efe4"

# The channel's recurring characters -- fixed here (not written by the model) so
# the figure looks identical across every upload. The model still chooses poses
# and expressions per beat.
HOST_LOOK = (
    "the 'Anti Broke' host: a minimalist black-marker stick figure with a round "
    "OPEN white head, four or five short spiky lines of hair on top, two small "
    "dot eyes, two short sharp angled eyebrows, a small mouth. The body is ONE "
    "single straight vertical black line (spine), with four more single straight "
    "black lines for the two arms and two legs, dot hands, short line feet -- "
    "plus one small thin necktie shape (a short strip + a little triangle) "
    "hanging at the neck. NO torso shape, NO filled body, NO solid black wedge, "
    "NO shirt block -- the figure is a head, spiky hair, five thin lines and a "
    "skinny tie. It is never a black silhouette."
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

SYSTEM = """You are the head writer for "Anti Broke", a faceless personal-finance YouTube Shorts channel.
You write tight, accurate scripts that a stick-figure animation pipeline turns into a vertical video.

Voice: a sharp market analyst who's a little pissed off on the viewer's behalf. Dry, calm, specific.
The edge comes from exposing how rigged the fine print is -- never from jokes, puns, or mocking the viewer.

The bar is HIGH. Every video must be genuinely surprising -- the kind of thing a smart person watches
and thinks "wait, WHAT". If the idea wouldn't make someone stop scrolling and say that out loud, pick a
sharper angle on the topic or a more shocking number. No "what is a budget", no generic advice, no
buildup -- open on the surprising conclusion, then show the mechanism. You explain how the machine
works; you never tell anyone what to buy or give individualised advice."""

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
        "props": ["at most ONE prop per beat, chosen ONLY from the PROP VOCAB below (exact name), or omit"],
        "headline": "BEAT 1 ONLY, REQUIRED there: the hook as 2-5 words of huge on-screen text -- a number or a punch (\"$35. EVERY TIME.\", \"YOU'RE LOSING MONEY.\", \"$2.9 TRILLION.\"). Not a full sentence. Omit on every other beat.",
        "chart": {   // OPTIONAL -- use on a beat that cites a trend or 2+ real numbers, INSTEAD of a prop
          "type": "bar" | "line" | "hbar",
          "title": "<= 6 word chart title",
          "labels": ["2018","2020","2022","2024"],   // 2-6 short labels
          "values": [80, 180, 340, 520],             // plain numbers, same length as labels
          "unit": "$B" | "%" | "",
          "highlight": <index of the value the narration calls out>,
          "note": "<= 4 word red callout, or omit"
        }
      }
      // 6 to 8 beats, targeting 20-28 seconds of narration total. Every character
      // mentioned in a beat's cast must be in the top-level cast.
      //
      // BEAT 1 is the whole game -- it decides whether anyone watches beat 2.
      //   `say`: the spoken hook, <= 12 words, ONE of:
      //     - a shocking specific number ("Your bank makes about thirty-five dollars every time you overdraft.")
      //     - the trick stated plainly ("Your card company can reorder your purchases so more of them bounce.")
      //     - a claim that sounds wrong but isn't ("Paying the minimum on a $5,000 card takes over twenty years.")
      //     - loss framed at the viewer ("Right now you're paying interest on things you already paid off.")
      //     - a stakes/aspiration flip ("Two people invest the same money. One ends up with double. Here's why.")
      //   `headline`: 2-5 words of huge text that IS the hook visually -- the number or the punch.
      //   `cast`: the host reacting to it (pointing at it, arms wide, unimpressed, alarmed).
      //   NEVER a definition, NEVER "let me explain", NEVER a soft yes/no question. Open on the payoff.
      // Middle beats: each one must carry a real number, a concrete image, or a sharp turn -- no filler
      //   transition lines. Name the villain: the fine print, the default setting, the fee schedule.
      // Last beat: a memorable one-liner the viewer could repeat -- not a summary, not a call to action.
      // EVERY beat needs something on screen besides the host: a prop, a chart, or (beat 1) the headline.
      //   A beat that is just the host talking is a dead frame -- give the last beat a prop too (the
      //   villain object: the contract, the fine print, the fee schedule, the default toggle).
      // At most one prop OR one chart per beat. 1-2 beats with a chart is ideal
      //   for a data topic; the chart's numbers MUST be ones the narration states
      //   and MUST be roughly accurate. A beat with a chart should not also list a prop.
      // Props must be concrete and instantly readable -- not abstract.
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
            scenes[name] = {"color": STAGE_COLOR}   # one consistent flat backdrop
        only = next(iter(scenes))
        for beat in script_obj.get("beats", []):
            beat["scene"] = only

    # drop any prop the model invented that isn't in the committed icon library
    allowed = set(icons.names())
    if allowed:
        for beat in script_obj.get("beats", []):
            beat["props"] = [p for p in (beat.get("props") or []) if p in allowed][:1]

    # beat 1 must have a hook headline -- synthesise one from the line if missing
    beats = script_obj.get("beats", [])
    if beats and not beats[0].get("headline"):
        say = beats[0].get("say", "")
        m = re.search(r"\$?\d[\d,]*(?:\.\d+)?\s*(?:billion|trillion|million|percent|%|dollars?)?", say)
        beats[0]["headline"] = (m.group(0).strip() if m
                                else " ".join(say.split()[:4]).rstrip(".,"))
        beats[0]["props"] = []


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60] or "video"


def generate(out_dir: Path | None = None, dry_topic: str | None = None) -> tuple[Path, dict]:
    themes = _load_themes()
    history = _history()
    AUTO_DIR.mkdir(parents=True, exist_ok=True)
    date = dt.date.today().isoformat()

    # reuse a script already written today but not yet published (a hand-reviewed
    # one, or a run that died after generate) instead of burning another LLM call
    media = Path("media")
    published = {p.stem for p in media.glob("*.mp4")} if media.exists() else set()
    for yml in sorted(AUTO_DIR.glob(f"{date}-*.yaml"), reverse=True):
        if yml.stem in published:
            continue
        meta_p = yml.with_suffix(".meta.json")
        meta = json.loads(meta_p.read_text()) if meta_p.exists() else {"slug": yml.stem}
        print(f"[generate] reusing existing script {yml.name}")
        return yml, meta

    topic = dry_topic or _pick_topic(themes, history)
    print(f"[generate] topic: {topic}")

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
