"""Stage 0: pick the next topic and write a full script -- no human in the loop.

Reads themes.yaml, chooses the least-recently-used topic that's outside the
cooldown window, asks Gemini (Vertex) to write a script + YouTube metadata,
validates it by loading it through script_model, and writes:

    scripts/auto/<date>-<slug>.yaml       the script
    scripts/auto/<date>-<slug>.meta.json  {title, description, tags}
    state/topic_history.json              appended (committed back by CI)

Every script generated here is CINEMATIC format (see assets.py/script_model.py):
one consistent character, Sarah, full-bleed photoreal scenes per beat, instead
of the channel's original flat-vector faceless cutout-on-flat-stage look.
Locked in 2026-09-17 after a live test published well (see memory /
llms.txt for the before/after) -- the flat-vector pipeline code is untouched
and still runnable by hand for a manually-authored script, but this
generator no longer produces it. Voices/identity are assigned here, not by
the model, so the channel keeps one consistent character across every video.
"""
from __future__ import annotations

import datetime as dt
import json
import random
import re
from pathlib import Path

import yaml

from . import config, script_model

TEXT_MODELS = [("us-central1", "gemini-2.5-pro"), ("us-central1", "gemini-2.5-flash")]

# The channel's recurring characters -- fixed here (not written by the model)
# so they look and sound identical across every upload. Wardrobe/style
# matches the reference sheet that shipped in the first published cinematic
# video: navy three-piece suit, red tie, gold accents. The model still writes
# a fresh scene (environment, pose, action) for every beat.
#
# Sarah is the default narrator for every auto-generated video (single-
# character solo narration, same as the validated first upload). Mike is
# locked and available as a second character for a future two-hander/
# dialogue format, but `_inject_identity` below doesn't use him yet -- solo
# Sarah is still the only shape `generate()` produces.
SARAH_VOICE = "Aoede"
SARAH_LOOK = (
    "Sarah, the channel's recurring host: a sharp, confident woman with long "
    "dark hair, wearing a tailored navy three-piece suit, a red silk tie, a "
    "crisp white dress shirt, and gold accents (a wristwatch, a ring) -- "
    "authority and wealth, rendered in the channel's premium cinematic style. "
    "A small lime-green circular brand patch sits discreetly on her left lapel."
)
MIKE_VOICE = "Orus"
MIKE_LOOK = (
    "Mike, the channel's recurring second character: a handsome man with a "
    "light Mediterranean or Latino complexion -- think a light-skinned "
    "Italian or light-skinned Mexican -- fair-to-light olive skin (NOT dark "
    "or deeply tanned skin), short curly dark brown hair, brown eyes, and a "
    "sharp, defined jawline. Wearing a "
    "tailored charcoal three-piece suit, a red silk tie, a crisp white dress "
    "shirt, and gold accents (a wristwatch, a ring) -- authority and wealth, "
    "rendered in the channel's premium cinematic style. A small lime-green "
    "circular brand patch sits discreetly "
    "on his left lapel."
)

STATE = Path("state/topic_history.json")
AUTO_DIR = Path("scripts/auto")

SYSTEM = """You are the head writer for "Anti Broke", a personal-finance YouTube Shorts channel
fronted by one consistent host character, Sarah. You write tight, accurate scripts that a
cinematic animation pipeline turns into a vertical video: one richly detailed photoreal scene
per beat, Sarah narrating throughout.

Voice: a sharp market analyst who's a little pissed off on the viewer's behalf. Dry, calm, specific.
The edge comes from exposing how rigged the fine print is -- never from jokes, puns, or mocking the viewer.

Two hooks, not one. Beat 1 stops the scroll; beat 2 (the "rehook") has to re-earn the next ten
seconds -- the second-biggest drop-off is the moment right after the hook. Beat 2 raises the stakes,
twists the knife, or names exactly who this happens to. It is NEVER a smooth transition, a definition,
or "let me explain".

Keep the language plain -- a sharp eighth-grader should follow every line on the first listen. Everyday
words; if you must use a term of art, define it in the same breath. Plain does NOT mean choppy: write
the way a person actually TALKS, not ad-copy. A real person doesn't say "No. On the entire amount. From
day one." -- they say "No -- the entire amount, from day one," in one breath, with normal connecting
words (and, so, because, but) and contractions (it's, you're, that's). Cut a line into short fragments
only where a real person would actually pause for effect, not on every single beat. If it reads like a
movie-trailer voiceover instead of a person mid-conversation, rewrite it.

Never write a line that trails off with "..." -- it reads fine on screen but the voice model
audibly stutters or restarts on it. End every beat on a normal full stop, comma, or dash instead,
even a beat that's conceptually "unfinished" (the NEXT beat still lands the twist).

The bar is HIGH. Every video must be genuinely surprising -- the kind of thing a smart person watches
and thinks "wait, WHAT". If the idea wouldn't make someone stop scrolling and say that out loud, pick a
sharper angle on the topic or a more shocking number. No "what is a budget", no generic advice everyone
already knows, no buildup -- open on the surprising conclusion, then show the mechanism.

Two content pillars, both in play: (1) the fine-print mechanic nobody explains, (2) the "what if you'd
invested" / market-history reveal real finance creators go viral with -- a real, well-known stock, index,
or asset, a real (rounded, widely-cited) dollar outcome, landing on a lesson about time in the market or
compounding. You explain how the machine works or what history actually shows; you never tell anyone
what to buy today, promise future returns, or give individualised advice.

Sameness is a real failure mode, not just a style nitpick -- a channel where every video opens the same
way and marches through beats the same shape is boring no matter how good any single fact is. The topic
is the WHAT; the FORMAT/STRUCTURE instruction you're given below is the HOW, and you follow it, not your
own default. If you're given "what if you'd invested" material, do NOT just reach for "$1,000 in
[company]'s IPO is worth $X today" again -- that shape has already been used repeatedly. Find the angle
in the specific topic that's actually interesting: the surprising REASON, the person it happened to, the
moment things could have gone the other way, the counterintuitive comparison -- not a fill-in-the-blank
template.

Never state a prediction, forecast, or a scheduled-but-uncertain future event as a fact. Do NOT open
"The Fed is about to raise rates", "rates are going up next month", "a recession is coming". Nobody
knows, and a viewer who does will call it out. If the topic hinges on something that MIGHT happen,
frame it conditionally the whole way through -- "When the Fed raises rates...", "Every time rates go
up...", "If that happens..." -- and the video must land its lesson on the timeless MECHANIC, which is
true whether or not the event ever occurs. If the hook leans on the maybe-event at all, an early beat
has to say the quiet part out loud ("Nobody knows if they actually will. But the day they do, here's
what your bank does."). Never imply a recent event that did not happen -- no "again", "another",
"back to", "still" about a move unless that prior move is real and you are certain of it."""

SCHEMA_DOC = """Return ONLY a JSON object, no prose, with this shape:

{
  "slug": "kebab-case-topic-slug",
  "title": "YouTube title, <=70 chars, specific, no clickbait punctuation spam",
  "description": "2-3 sentence description, then a blank line, then 3-5 hashtags",
  "tags": ["personal finance", "..."],           // 5-10 short tags
  "script": {
    "title": "same as title",
    "slug": "same as slug",
    "format": "short",
    "beats": [
      {
        "id": "b01",
        "say": "ONE spoken beat, natural sentence rhythm -- punchy on the hook, can run longer on a beat that's genuinely explaining something, never a paragraph, NEVER trailing off with '...'",
        "scene": "A FRESH, richly detailed description of THIS beat's shot only -- never reused from another beat. Must specify: (1) the environment/location -- ALWAYS a visually rich, eye-catching space with real colour/material/texture (warm mahogany-and-gold or cool marble-and-steel), NEVER a bare/sterile/plain-white set (no plain white desks, walls, or blank white screens as generic backdrop), (2) Sarah's exact pose, action, and facial expression in it, (3) the lighting. If any document, sign, screen, or object in the shot carries text, WRITE OUT THAT EXACT TEXT here in quotes -- if you don't specify text for an object, the renderer puts NONE on it, so never leave a prop's wording to be invented. A number written on a prop/sign in this beat MUST be the identical figure this beat's `say` states (if `say` says 'fifty thousand dollars', the sign must read '$50,000', not a rounder or different figure). If Sarah makes a counting gesture (holding up fingers), the count must exactly match a number named in `say`. Prefer real, literal objects (a filing cabinet, a folder, a bank statement, a phone screen) over a symbolic/storybook stand-in for an idea (no glowing treasure chests for a company, no funnel spitting coins for a merger of funds). If Sarah is reading data off her OWN device (a laptop/phone she's looking at), don't put the readable text on that device's screen while also describing her facing the camera -- a screen can't face both her and the camera at once; either describe an over-the-shoulder framing, or put the readable text on a wall-mounted monitor, a printed document, or a sign instead.",
        "tone": "OPTIONAL: set to \\"negative\\" on a beat about a loss, a fee, a trap, a threat, or the villain -- the pipeline washes a red edge-vignette over the frame so the visuals match the sting. Omit on neutral, hopeful, or payoff beats. Use it on the 1-3 beats that genuinely bite, never on every beat."
      }
      // As many beats as the story genuinely needs -- end when the explanation and
      // payoff are actually complete, not on a fixed beat count. Don't pad or repeat
      // a point to stretch it, and don't cut the mechanism short to rush to the CTA
      // either. A tight, complete story at 20 seconds is just as valid as one that
      // needs 60 -- length follows the explanation, not the other way round. The one
      // real ceiling: Instagram stops treating a video as a Reel past ~90 seconds of
      // narration, so land it under that.
      //
      // BEAT 1 is the whole game -- it decides whether anyone watches beat 2.
      //   `say`: the spoken hook, <= 12 words, ONE of:
      //     - a shocking specific number ("Your bank makes about thirty-five dollars every time you overdraft.")
      //     - the trick stated plainly ("Your card company can reorder your purchases so more of them bounce.")
      //     - a claim that sounds wrong but isn't ("Paying the minimum on a $5,000 card takes over twenty years.")
      //     - loss framed at the viewer ("Right now you're paying interest on things you already paid off.")
      //     - a stakes/aspiration flip ("Two people invest the same money. One ends up with double. Here's why.")
      //   `scene`: Sarah reacting to it -- pointing at something, arms wide, unimpressed, alarmed -- in a
      //     scene that visually states the hook (the exact number/figure on a document, sign, or screen).
      //   NEVER a definition, NEVER "let me explain", NEVER a soft yes/no question. Open on the payoff.
      // BEAT 2 IS THE REHOOK -- not a transition. Re-hook the viewer who nearly swiped: restate the
      //   stakes a sharper way, add the detail that makes it worse or bigger, or name exactly who this
      //   lands on. The hole should feel like it just got deeper, not like the video is settling in.
      // Middle beats should flow causally from the one before -- "because of that", "which meant",
      //   "so next" -- like you're actually telling someone the story, not reciting a list of facts
      //   at them. Each one still has to earn its place with a real number, a concrete image, or a
      //   turn -- connected is not the same as padded. Name the villain: the fine print, the default
      //   setting, the fee schedule.
      // Second-to-last beat: land the same punch you opened with -- a one-liner that could loop
      //   straight back into beat 1, a memorable line the viewer could repeat -- not a summary,
      //   not a call to action.
      // LAST beat, always, is the call-to-action -- write it yourself, don't skip it. It must
      //   still prompt two actions (comment + save), but phrase them so they clearly react to
      //   THIS video's specific topic/number/mechanism, not a generic template -- e.g. for a video
      //   about a hidden fee: "Comment if a bank's ever done this to you, and save this before it
      //   happens again" -- not "comment your take below". Give it its own `scene`: Sarah addressing
      //   the viewer directly, warm and inviting, same rules as every other scene (rich environment,
      //   no plain white, deliberate camera choice).
    ]
  }
}

CAMERA CRAFT -- part of what made the channel's best-performing video work was
real shot variety, not just good writing. Every beat's `scene` must choose a
shot type on purpose, matched to what that beat needs the viewer to FEEL.
Never default to the same eye-level medium "she's facing the camera" shot for
every beat -- that reads flat and static no matter how good the writing is.
Draw from:
- Extreme close-up (a hand, a signature, a stamped seal, a single object) for
  an intimate, ominous, or pivotal reveal -- isolates one detail and creates
  tension.
- Low angle (camera below eye level, looking up at her) for a beat about
  authority, power, or confidence -- reads as commanding.
- High angle (camera above, looking down) for vulnerability, being caught
  out, or something going wrong -- reads as small/exposed. Pairs naturally
  with `tone: negative`.
- Wide/establishing shot (the whole room, the whole building, a skyline) for
  scale or grandeur, or to plant a new location -- lets the environment sell
  the stakes instead of her expression.
- Eye-level medium shot as the connective/default for a plain explanation
  beat -- direct and conversational, but should NOT be every beat's choice.
- A slightly tilted/off-kilter angle, used sparingly, for a beat about a
  trap, a threat, or something being wrong.
Before finalizing, scan the sequence of shots across the whole video: if
three beats in a row land on the same distance/angle, change one. The result
should read like a director made deliberate choices shot to shot, not a
slideshow of the same composition against different backdrops.

SCRIPTING STYLE -- the strongest shape, and the one to reach for by default,
follows ONE concrete throughline like a documentary exposing exactly how she
operates: not a list of facts, but a mechanism or decision path revealed
layer by layer, beat by beat ("First she does X. That gets her Y. Then Z
becomes possible..."). That reads as a story with momentum. Reach for a
structured side-by-side comparison instead only when the topic is genuinely
several disconnected categories with no causal chain between them (e.g.
comparing discrete net-worth tiers) -- and even then, camera variety (above)
still matters just as much.

Rules:
- Accurate. Any figure used must be roughly correct.
- No predictions as fact. Never say a future or uncertain event WILL happen ("the Fed is about to
  hike", "rates are going up", "a crash is coming"). Frame it conditionally end to end ("when rates
  rise...", "if that happens...") and land the lesson on the timeless mechanic. If the hook uses the
  maybe-event, one early beat must acknowledge the uncertainty out loud. No "again"/"another"/"back to"
  implying a recent event unless it definitely happened.
- Use financial terms precisely or not at all. "Net interest margin" is a ratio, not "the difference";
  the gap between deposit and loan rates is the "interest rate spread". Don't name a term you're using loosely.
- Plain language: an eighth-grader follows every line first time. Everyday words, short sentences,
  no unexplained jargon -- a "reads at grade 8 or under" check runs before publish.
- Never tell the viewer to buy anything today, and never promise future returns. No hype phrasing.
- A "what if you'd invested" / market-history topic MAY name a real, well-known company, index, or asset
  (Apple, Amazon, Bitcoin, the S&P 500, Berkshire Hathaway, ...) -- strictly past tense ("would have grown
  to roughly $X"), round widely-cited public figures only (never a suspiciously precise number), and it
  must land on a lesson (time in the market, compounding, diversification) -- never framed as "buy this now".
- Every `say` must land in about 1-2 seconds of speech. Short. Punchy. Spoken, not written. Never ends
  with "..." (see above -- it destabilises the voice model).
- Sarah is the ONLY character. Every scene features her (unless a beat is explicitly a cutaway to an
  unnamed third party she's describing, which should be rare).
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
    eligible = [t for t in topics if t not in recent] or topics
    # random among whatever's outside the cooldown window, not a fixed
    # order -- a deterministic pick is still a pattern a frequent viewer
    # can eventually notice, even if the cycle is long.
    return random.choice(eligible)


def _ask(topic: str, themes: dict, structure_hint: str) -> dict:
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
        f"STRUCTURE FOR THIS ONE (not your choice -- the channel rotates structures so "
        f"every video doesn't look the same): {structure_hint}\n\n"
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
    """Force Sarah's identity, voice, and the cinematic render pipeline onto
    whatever the model returned, so the channel's identity is stable across
    uploads -- the model never picks the character or the visual format."""
    script_obj["cast"] = {"sarah": {"voice": SARAH_VOICE, "look": SARAH_LOOK}}
    script_obj.setdefault("narrator", {})["voice"] = SARAH_VOICE
    script_obj["cinematic"] = True
    script_obj["caption_style"] = "cinematic"

    scenes: dict = {}
    beats = script_obj.get("beats", [])
    for i, beat in enumerate(beats):
        bid = str(beat.get("id") or f"b{i:02d}").strip()
        beat["id"] = bid
        raw_scene = str(beat.pop("scene", "") or "").strip()
        sid = f"s_{bid}"
        scenes[sid] = {"bg": raw_scene}
        beat["scene"] = sid
        # single-character narration -- no per-beat cast/pose dict, no prop
        # icons, no chart overlay in the cinematic pipeline; the scene text
        # above is the entire visual for the beat.
        beat.pop("cast", None)
        beat.pop("props", None)
        beat.pop("chart", None)
        beat.pop("headline", None)
    script_obj["scenes"] = scenes

    _append_cta(script_obj)


# SCHEMA_DOC now requires the model to write its own final CTA beat, phrased
# to react to THIS video's specific topic (Dev: a bolted-on generic line
# "almost never fits our reel" -- true, it doesn't reference anything the
# video actually said). This is now a FALLBACK ONLY, for the rare case the
# model skips it -- never the primary path. If it fires often, that's a sign
# SCHEMA_DOC's instruction needs strengthening, not that these lines need
# expanding back out.
# (These generic lines intentionally avoid presupposing a debate -- "which
# side are you on"/"agree or disagree" doesn't fit most of this channel's
# "here's a hidden mechanism" content, confirmed by Dev's own feedback.)
_CTA_LINES = [
    "Comment your take below, then save this so future-you remembers.",
    "If this surprised you, comment below and save it for later.",
    "Comment what surprised you most about this, and save it for later.",
    "Tell me in the comments if you knew this already, and save it for later.",
]
_CTA_SCENES = [
    "Sarah leans casually against the edge of her mahogany desk, warm confident smile, "
    "one hand gesturing invitingly out toward camera. Soft evening light through a "
    "large office window behind her, city lights beginning to glow outside.",
    "Sarah closes a leather folder on her desk and looks directly at camera with a "
    "warm, knowing smile, one hand open toward the viewer in an inviting gesture. "
    "Warm desk-lamp light, soft blue dusk through the window behind her.",
]


def _append_cta(script_obj: dict) -> None:
    """Fallback only -- see comment above _CTA_LINES. Does nothing if the
    model's own last beat already reads like a CTA (mentions commenting or
    saving), which should be the normal case now."""
    beats = script_obj.get("beats", [])
    if not beats:
        return
    last_say = str(beats[-1].get("say", "")).lower()
    if "comment" in last_say or "save" in last_say:
        return
    n = len(beats)
    say = random.choice(_CTA_LINES)
    scene_text = random.choice(_CTA_SCENES)
    bid = f"cta{n:03d}"
    sid = f"s_{bid}"
    script_obj.setdefault("scenes", {})[sid] = {"bg": scene_text}
    beats.append({"id": bid, "scene": sid, "say": say})
    print("  ! model didn't write its own CTA beat -- used the generic fallback")


# Picked LRU, same mechanism as _pick_topic (not a fixed rotation -- a fixed
# cycle of even a dozen shapes is still a pattern a 3x/day viewer notices
# within days). Solo-narration structures only (Sarah is the one recurring
# character now -- see module docstring); the earlier two-character/skit
# directions (myth-vs-reality, debate, interview, ...) are retired along
# with the flat-vector pipeline they were written for.
_DIRECTIONS = [
    {"id": "reveal",
     "hint": "State the surprising fact plainly, then reveal the mechanism. Direct and sharp -- the baseline shape, not a crutch."},
    {"id": "then-vs-now",
     "hint": "Anchor on a concrete before/after comparison across time (a price, a payout, a rule) so the scale of change is visceral, not abstract."},
    {"id": "insider-reveal",
     "hint": "Delivered like someone leaking a secret the industry doesn't want said out loud -- conspiratorial energy, not a lecture."},
    {"id": "countdown",
     "hint": "A numbered countdown/listicle shape -- each beat is a distinct point, building to the sharpest one last."},
    {"id": "explain-like-five",
     "hint": "Radically simplify -- explain it the way you'd explain it to a confused friend who's never heard of this, leaning on the most everyday analogy you can find."},
    {"id": "news-flash",
     "hint": "Delivered like a breaking-news anchor cutting in with urgent energy -- headline-first, short declarative bursts."},
    {"id": "timeline-walk",
     "hint": "Walk chronologically through a sequence of moments/events, each beat one step forward in time, building to the payoff at the end."},
]


def _pick_direction(history: list[dict]) -> dict:
    ids = [d["id"] for d in _DIRECTIONS]
    cooldown = max(len(_DIRECTIONS) - 3, 4)
    recent = {h["direction"] for h in history[-cooldown:] if h.get("direction")}
    eligible = [d for d in ids if d not in recent] or ids
    chosen = random.choice(eligible)
    return next(d for d in _DIRECTIONS if d["id"] == chosen)


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
    direction = _pick_direction(history)
    structure_hint = f"[{direction['id']}] {direction['hint']}"
    print(f"[generate] topic: {topic}")
    print(f"[generate] direction: {direction['id']}  |  {direction['hint'][:70]}...")

    obj = None
    for attempt in range(3):
        cand = _ask(topic, themes, structure_hint)
        script_obj = cand["script"]
        script_obj["slug"] = f"{date}-{_slugify(cand.get('slug') or topic)}"
        script_obj["title"] = cand.get("title") or script_obj.get("title") or topic
        # a slug colliding with an already-published video means the model
        # (or a reworded themes.yaml entry) landed on essentially the same
        # short again -- overwriting it would silently re-render + re-publish
        # a duplicate, which is worse than burning one more LLM call.
        if script_obj["slug"] in published:
            print(f"  slug collides with an already-published video "
                 f"(attempt {attempt + 1}): {script_obj['slug']}")
            continue
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
        raise RuntimeError(f"could not produce a valid, non-duplicate script for: {topic}")

    meta = {
        "topic": topic,
        "title": obj["title"] if "title" in obj else script_obj["title"],
        "description": obj.get("description", ""),
        "tags": obj.get("tags", []),
        "slug": script_obj["slug"],
        "date": date,
    }
    (AUTO_DIR / f"{script_obj['slug']}.meta.json").write_text(json.dumps(meta, indent=2))

    history.append({"date": date, "topic": topic, "slug": script_obj["slug"],
                    "direction": direction["id"]})
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(history, indent=2))

    print(f"[generate] wrote {path}  ({len(script_obj['beats'])} beats)")
    return path, meta


if __name__ == "__main__":
    import sys
    generate(dry_topic=sys.argv[1] if len(sys.argv) > 1 else None)
