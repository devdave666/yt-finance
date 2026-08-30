"""Central config for the stick-figure pipeline.

GCP: Dev's real project -- the SAME one core-decor-automation uses for Vertex
image generation (see that repo's llms.txt). Auth is ADC
(`gcloud auth application-default login`). The $300 Vertex trial credit covers
image gen + TTS; it does NOT cover the AI Studio API, so every genai call goes
through vertexai=True.
"""
import os

GCP_PROJECT = os.environ.get("GCP_PROJECT", "project-58f4f689-36b9-406b-bfa")

# gemini-2.5-flash-image ("Nano Banana"): confirmed working for this project in
# us-central1 (core-decor verified 2026-08-22). No "-preview" suffix. Neither
# 2.5 nor 3.1 outputs real alpha -- we cut assets out with rembg instead.
# gemini-3.1-flash-image exists too but ONLY at location="global".
IMAGE_MODEL = os.environ.get("STICKFIN_IMAGE_MODEL", "gemini-2.5-flash-image")
IMAGE_LOCATION = os.environ.get("STICKFIN_IMAGE_LOCATION", "us-central1")

# Background-removal model for turning generated art into transparent cutouts.
# "isnet-general-use" is the best general matte; "u2netp" is faster/lighter.
REMBG_MODEL = os.environ.get("STICKFIN_REMBG_MODEL", "isnet-general-use")

# ---- Output format ------------------------------------------------------
# Shorts/Reels first (all of Dev's reference reels are 9:16); wide for the
# long-form phase later.
FORMATS = {"short": (1080, 1920), "wide": (1920, 1080)}
DEFAULT_FORMAT = os.environ.get("STICKFIN_FORMAT", "short")
FPS = 30


def canvas(fmt: str | None = None) -> tuple[int, int]:
    return FORMATS[fmt or DEFAULT_FORMAT]


def aspect_ratio(fmt: str | None = None) -> str:
    return "9:16" if (fmt or DEFAULT_FORMAT) == "short" else "16:9"


# ---- Cut cadence -------------------------------------------------------------
# No single drawing holds longer than MAX_HOLD_S -- a beat whose narration runs
# longer is split into that many holds (identical composite unless the beat
# defines `steps`). Skit lines are naturally short; explainer beats lean on
# phrase-by-phrase captions for motion during a hold.
MAX_HOLD_S = float(os.environ.get("STICKFIN_MAX_HOLD_S", "2.7"))
MIN_HOLD_S = float(os.environ.get("STICKFIN_MIN_HOLD_S", "0.9"))

# ---- Character layout (fraction of canvas) ---------------------------------
CHAR_HEIGHT_FRAC = 0.54       # default figure height vs canvas height
CHAR_BASELINE_FRAC = 0.95     # where feet sit
ANCHOR_X = {"far-left": 0.24, "left": 0.30, "center": 0.5,
            "right": 0.70, "far-right": 0.78}

# When a shot has an object, the speaker slides far-left and the object sits
# far-right, capped in width so it can't reach back across the figure.
CHAR_ANCHOR_WITH_PROPS = "far-left"
PROP_ZONE_AT = "far-right-low"
PROP_SCALE = 0.20
PROP_MAX_W_FRAC = 0.34       # never wider than this fraction of the canvas

# ---- Motion --------------------------------------------------------------
IDLE_BOB_PX = float(os.environ.get("STICKFIN_IDLE_BOB_PX", "6"))
IDLE_BOB_HZ = 0.5
POP_IN_S = 0.16             # new layers fade/scale in over this on each cut

# ---- Text-to-Speech ------------------------------------------------------
TTS_LANGUAGE = "en-US"
# Gemini-TTS: takes a natural-language `prompt` that steers delivery style, so
# the voice can carry actual character (review: "add more feeling"). Voice
# names are the Gemini roster (Charon/Puck/Kore/...); needs model_name.
TTS_MODEL = os.environ.get("STICKFIN_TTS_MODEL", "gemini-2.5-flash-tts")
DEFAULT_VOICE = os.environ.get("STICKFIN_VOICE", "Orus")
# Register modelled on the "market skeptic" finance explainers (ref: stickfigref1):
# credible analyst, dry and a little skeptical, NOT a comedian, NOT hype.
TTS_STYLE = os.environ.get("STICKFIN_TTS_STYLE", (
    "You explain money and markets on a channel called Anti Broke. Deliver this "
    "like a sharp market analyst walking someone through the case: calm, "
    "confident, a little skeptical, faintly amused at how rigged the fine print "
    "is. Brisk and clear -- keep it moving, hit the key numbers and the turn, "
    "then land the last line flat. No hype, no goofiness, and never draggy or "
    "sing-song."
))
TTS_SAMPLE_RATE = 48000
TTS_TARGET_LUFS = -15.0
TTS_SPEAKING_RATE = 1.16       # base pace hint to Gemini-TTS
TTS_MIN_WPS = 2.85           # hard floor: any beat slower than this gets sped up with atempo
BEAT_GAP_S = 0.03            # near-zero: dead air between lines was the #1 complaint
TTS_TRIM_SILENCE = True      # strip leading/trailing silence from each beat clip

# ---- Publishing (Buffer) ---------------------------------------------------
# Both channels live on one Buffer account (org "My organization"). Channel ids
# aren't secret -- only BUFFER_API_KEY is -- so they carry a default and the
# daily cron posts to both with no extra setup. Env vars override.
# (`or` not a default arg -- CI passes the var through as "" when the secret is unset)
BUFFER_YOUTUBE_CHANNEL_ID = (
    os.environ.get("BUFFER_YOUTUBE_CHANNEL_ID") or "6a935542ccaf649a674104fd")   # "Anti Broke"
BUFFER_INSTAGRAM_CHANNEL_ID = (
    os.environ.get("BUFFER_INSTAGRAM_CHANNEL_ID") or "6a93569eccaf649a6741110c")  # @antibrokee
BUFFER_TIKTOK_CHANNEL_ID = (
    os.environ.get("BUFFER_TIKTOK_CHANNEL_ID") or "6a942e4f065799be46540c7b")    # @antibrokee

# ---- Ken Burns (off by default; the reference style has a static camera) ----
KENBURNS = os.environ.get("STICKFIN_KENBURNS", "0") == "1"

# ---- SFX ---------------------------------------------------------------------
SFX_TICKS = os.environ.get("STICKFIN_SFX_TICKS", "0") == "1"   # per-cut click; off (annoying)

# ---- Captions -----------------------------------------------------------
# ASS colours are &HAABBGGRR. White text with a heavy near-black outline (reads
# on any background); the word currently being said flips to amber.
CAP_SPOKEN = "&H0018C6FF"     # #FFC618 amber
CAP_PENDING = "&H00FFFFFF"    # white
CAP_OUTLINE = "&H00181818"    # near-black
ZOOM_RATE_PER_S = 0.05
MAX_ZOOM = 1.25
