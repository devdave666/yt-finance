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
MAX_HOLD_S = float(os.environ.get("STICKFIN_MAX_HOLD_S", "1.8"))
MIN_HOLD_S = float(os.environ.get("STICKFIN_MIN_HOLD_S", "0.9"))

# ---- Character layout (fraction of canvas) ---------------------------------
CHAR_HEIGHT_FRAC = 0.56       # default figure height vs canvas height
CHAR_BASELINE_FRAC = 0.94     # where feet sit
ANCHOR_X = {"left": 0.28, "center": 0.5, "right": 0.72}

# ---- Text-to-Speech ------------------------------------------------------
TTS_LANGUAGE = "en-US"
DEFAULT_VOICE = os.environ.get("STICKFIN_VOICE", "en-US-Neural2-D")
TTS_SAMPLE_RATE = 48000
TTS_TARGET_LUFS = -16.0
BEAT_GAP_S = 0.15

# ---- Ken Burns (off by default; the reference style has a static camera) ----
KENBURNS = os.environ.get("STICKFIN_KENBURNS", "0") == "1"
ZOOM_RATE_PER_S = 0.05
MAX_ZOOM = 1.25
