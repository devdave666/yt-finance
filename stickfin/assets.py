"""Build the reusable asset library, then generate it.

    plan_assets(script)      -> asset_plan.json   (pure planning, no API calls)
    generate_assets(script)  -> assets/**/*.png   (Nano Banana + rembg)

Asset kinds:
  bg/<scene>.png        opaque, fills the canvas, no characters, no text
  char/_<name>.png      character reference sheet (identity lock)
  char/<name>__<state>.png   transparent cutout of one pose+expression
  prop/<name>.png       transparent cutout of a single object
  cutout/<hash>.png     transparent cutout of an ingested photo / frame

Everything is cached by path -- a re-run only makes what's missing, so a crash
mid-way never re-spends. Same genai call pattern core-decor proved:
genai.Client(vertexai=True) + generate_content + inline_data, 429 backoff.
"""
from __future__ import annotations

import hashlib
import json
import re
import time
import urllib.request
from io import BytesIO
from pathlib import Path

from . import config

STYLE_FLOOR = (
    "Hand-drawn / clean-vector explainer aesthetic. Thick consistent solid "
    "black outlines, flat fills, no gradients, no drop shadows, no photoreal "
    "rendering. Bold and legible."
)
CHAR_FLOOR = (
    "Stick figure: perfectly circular head, thick even black outline, dot "
    "eyes, simple line mouth. Limbs are single thick black lines. Keep the "
    "figure EXACTLY on-model versus the reference sheet -- same proportions, "
    "same line weight, same colours."
)
# Flat mid-grey keys out cleanly against both black outlines and white fills.
MATTE_BG = "on a completely flat solid #8a8a8a grey background, no gradient, no shadow, no floor line, no horizon"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "x"


def _src_key(src: str) -> str:
    return hashlib.sha1(src.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------

def plan_assets(script) -> dict:
    scenes, characters, poses, props, cutouts = {}, {}, {}, {}, {}

    for name, sc in script.scenes.items():
        scenes[name] = {"bg": sc.bg, "color": sc.color}

    for name, ch in script.cast.items():
        characters[name] = {"look": ch.look}

    for beat in script.beats:
        if beat.is_live:
            cutouts.setdefault(_src_key(beat.live["src"]),
                               {"src": beat.live["src"], "kind": "live"})
            continue
        for cname, state in beat.cast.items():
            key = f"{cname}__{slug(state)}"
            poses[key] = {"char": cname, "state": state}
        for p in beat.props:
            props[slug(p)] = {"name": p}
        for co in beat.cutouts:
            cutouts.setdefault(_src_key(co.src), {"src": co.src, "kind": "image"})

    plan = {
        "slug": script.slug,
        "fmt": script.fmt,
        "scenes": scenes,
        "characters": characters,
        "poses": poses,
        "props": props,
        "cutouts": cutouts,
        "count": len(scenes) + len(characters) + len(poses) + len(props) + len(cutouts),
    }
    script.build_dir.mkdir(parents=True, exist_ok=True)
    (script.build_dir / "asset_plan.json").write_text(json.dumps(plan, indent=2))
    return plan


# --------------------------------------------------------------------------
# Generation
# --------------------------------------------------------------------------

def _client():
    from google import genai
    return genai.Client(vertexai=True, project=config.GCP_PROJECT,
                        location=config.IMAGE_LOCATION)


def _cfg(fmt: str):
    from google.genai import types
    return types.GenerateContentConfig(
        image_config=types.ImageConfig(aspect_ratio=config.aspect_ratio(fmt)))


def _generate(client, contents, cfg, retries: int = 5, base_delay: int = 20):
    from google.genai import errors as genai_errors
    for attempt in range(retries):
        try:
            return client.models.generate_content(
                model=config.IMAGE_MODEL, contents=contents, config=cfg)
        except genai_errors.ClientError as exc:
            if not ("429" in str(exc) or "RESOURCE_EXHAUSTED" in str(exc)) or attempt == retries - 1:
                raise
            time.sleep(base_delay * (2 ** attempt))


def _pil_from(response):
    from PIL import Image
    for cand in response.candidates or []:
        for part in (cand.content.parts or []):
            inline = getattr(part, "inline_data", None)
            if inline and getattr(inline, "data", None):
                return Image.open(BytesIO(inline.data)).convert("RGB")
    raise RuntimeError(f"no image in response: {response!r}"[:600])


_rembg_session = None


def _cutout(pil_rgb):
    """RGB PIL image -> RGBA with background removed and trimmed to content."""
    global _rembg_session
    from rembg import new_session, remove
    if _rembg_session is None:
        _rembg_session = new_session(config.REMBG_MODEL)
    out = remove(pil_rgb, session=_rembg_session, post_process_mask=True)
    bbox = out.getchannel("A").getbbox()
    if bbox:
        pad = 6
        x0, y0, x1, y1 = bbox
        out = out.crop((max(0, x0 - pad), max(0, y0 - pad),
                        min(out.width, x1 + pad), min(out.height, y1 + pad)))
    return out


def _load_src(src: str):
    from PIL import Image
    if src.startswith(("http://", "https://")):
        with urllib.request.urlopen(src, timeout=30) as r:  # noqa: S310
            return Image.open(BytesIO(r.read())).convert("RGB")
    p = Path(src)
    if p.suffix.lower() in {".mp4", ".mov", ".webm", ".mkv"}:
        from .ffmpeg_util import run_ffmpeg
        tmp = p.with_suffix(".still.png")
        run_ffmpeg(["-i", src, "-vf", "thumbnail", "-frames:v", "1", tmp],
                   f"still from {p.name}")
        img = Image.open(tmp).convert("RGB")
        tmp.unlink(missing_ok=True)
        return img
    return Image.open(p).convert("RGB")


def generate_assets(script, plan: dict, force: bool = False) -> None:
    from PIL import Image, ImageOps

    a = script.build_dir / "assets"
    for sub in ("bg", "char", "prop", "cutout"):
        (a / sub).mkdir(parents=True, exist_ok=True)

    cw, ch = config.canvas(script.fmt)
    client = _client()
    cfg = _cfg(script.fmt)

    # ---- backgrounds ----
    for name, sc in plan["scenes"].items():
        out = a / "bg" / f"{name}.png"
        if out.exists() and not force:
            continue
        if not sc["bg"]:
            color = sc["color"] or "#ffffff"
            Image.new("RGB", (cw, ch), color).save(out)
            print(f"  bg {name} (flat {color})")
            continue
        resp = _generate(client, [
            f"{STYLE_FLOOR}\n\n{sc['bg']}\n\nFull-bleed background filling a "
            f"{config.aspect_ratio(script.fmt)} vertical frame. No characters, "
            "no people, no text or captions. Leave the lower-middle area "
            "uncluttered for characters to stand in front of."], cfg)
        img = ImageOps.fit(_pil_from(resp), (cw, ch), method=Image.LANCZOS)
        img.save(out)
        print(f"  bg {name}")

    # ---- character reference sheets ----
    sheets = {}
    for name, c in plan["characters"].items():
        out = a / "char" / f"_{name}.png"
        if not out.exists() or force:
            resp = _generate(client, [
                f"{STYLE_FLOOR}\n{CHAR_FLOOR}\n\nCHARACTER: {c['look']}\n\n"
                f"Draw a reference sheet: this same character full-body, "
                f"front and 3/4 views, {MATTE_BG}. No other characters, no text."], cfg)
            _pil_from(resp).save(out)
            print(f"  char sheet {name}")
        sheets[name] = Image.open(out)

    # ---- poses (transparent) ----
    for key, spec in plan["poses"].items():
        out = a / "char" / f"{key}.png"
        if out.exists() and not force:
            continue
        c = plan["characters"][spec["char"]]
        resp = _generate(client, [
            f"{STYLE_FLOOR}\n{CHAR_FLOOR}\n\nCHARACTER: {c['look']}\n"
            f"POSE / EXPRESSION: {spec['state']}\n\n"
            f"ONLY the single character -- the whole figure head to feet, "
            f"centred, filling most of the frame vertically, {MATTE_BG}. "
            "Absolutely NO furniture, NO chair, NO desk, NO background objects, "
            "NO floor, NO other characters, and NO text -- just the figure "
            "(plus a held prop only if the pose explicitly names one). The "
            "first image is the reference sheet; stay exactly on-model.",
            sheets[spec["char"]]], cfg)
        cut = _cutout(_pil_from(resp))
        if cut.width > cut.height * 1.15:
            print(f"  ! pose {key} came out wide ({cut.width}x{cut.height}) "
                  "-- likely grabbed furniture; consider --force re-gen")
        cut.save(out)
        print(f"  pose {key}")

    # ---- props (transparent) ----
    for key, spec in plan["props"].items():
        out = a / "prop" / f"{key}.png"
        if out.exists() and not force:
            continue
        resp = _generate(client, [
            f"{STYLE_FLOOR}\n\nA single {spec['name']} -- one clean icon-like "
            f"object, centered, {MATTE_BG}. No text, no hands, no character."], cfg)
        _cutout(_pil_from(resp)).save(out)
        print(f"  prop {key}")

    # ---- cutouts (ingested, transparent) ----
    for key, spec in plan["cutouts"].items():
        if spec.get("kind") == "live":
            continue  # live clips are used as video, not stills
        out = a / "cutout" / f"{key}.png"
        if out.exists() and not force:
            continue
        _cutout(_load_src(spec["src"])).save(out)
        print(f"  cutout {key}  <- {spec['src']}")
