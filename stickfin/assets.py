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
import shutil
import time
import urllib.request
from io import BytesIO
from pathlib import Path

from . import config, icons

STYLE_FLOOR = (
    "Hand-drawn / clean-vector explainer aesthetic. Thick consistent solid "
    "black outlines, flat fills, no gradients, no drop shadows, no photoreal "
    "rendering. Bold and legible."
)
CHAR_FLOOR = (
    "The figure is thin LINE ART: an open round white head (with a few spiky "
    "hair lines) plus five single straight lines for the body (spine, 2 arms, "
    "2 legs), dot hands, and one small skinny necktie at the neck. It has NO "
    "torso shape and is NEVER a filled black silhouette or a solid body wedge. "
    "Open white head, dot eyes, sharp angled brows, one even black line weight. "
    "Match the reference sheet exactly for proportions and construction."
)
# Flat mid-grey keys out cleanly against both black outlines and white fills.
MATTE_BG = "on a completely flat solid #8a8a8a grey background, no gradient, no shadow, no floor line, no horizon"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:48] or "x"


def _flat_bg(color: str, w: int, h: int):
    """A flat colour field with a soft corner vignette + faint grain -- warmer
    than dead flat, and with none of the 'framed poster' borders an image model
    keeps drawing around a 'backdrop'."""
    import numpy as np
    from PIL import Image
    base = np.array(Image.new("RGB", (w, h), color), dtype=np.float32)
    ys, xs = np.mgrid[0:h, 0:w]
    d = np.hypot((xs - w / 2) / (w / 2), (ys - h / 2) / (h / 2))
    vig = np.clip(1.0 - 0.16 * np.clip(d - 0.4, 0, None) ** 2, 0.8, 1.0)[..., None]
    grain = (np.random.default_rng(7).random((h, w, 1)) - 0.5) * 5
    out = np.clip(base * vig + grain, 0, 255).astype(np.uint8)
    return Image.fromarray(out)


def _src_key(src: str) -> str:
    return hashlib.sha1(src.encode()).hexdigest()[:12]


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------

def plan_assets(script) -> dict:
    scenes, characters, poses, props, cutouts, charts_, headlines_ = {}, {}, {}, {}, {}, {}, {}

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
        if beat.headline:
            headlines_[beat.id] = beat.headline
        if beat.chart:
            charts_[beat.id] = beat.chart
        elif not beat.headline:
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
        "charts": charts_,
        "headlines": headlines_,
        "count": (len(scenes) + len(characters) + len(poses) + len(props)
                  + len(cutouts) + len(charts_) + len(headlines_)),
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


def _pil_or_none(response):
    """PIL image, or None when the model answered with text instead of an image."""
    try:
        return _pil_from(response)
    except RuntimeError:
        return None


_rembg_session = None


def _cutout(pil_rgb, ink: bool = False):
    """RGB PIL image -> RGBA with background removed and trimmed to content.

    ink=True normalises the line art: any grey/washed-out stroke is forced to
    solid near-black so the figure reads the same weight in every pose (Nano
    Banana sometimes draws a pose in faint grey ink)."""
    global _rembg_session
    from PIL import Image, ImageFilter
    from rembg import new_session, remove
    if _rembg_session is None:
        _rembg_session = new_session(config.REMBG_MODEL)
    out = remove(pil_rgb, session=_rembg_session, post_process_mask=True)

    if ink:
        import numpy as np
        arr = np.asarray(out).copy()
        lum = arr[..., :3].mean(axis=2)
        opaque = arr[..., 3] > 60
        dark = opaque & (lum < 190)          # every stroke, however faint
        arr[dark, 0:3] = 22
        arr[dark, 3] = 255
        out = Image.fromarray(arr)

    # shrink the matte by ~1px to eat the pale antialiased fringe (shows as a
    # white halo when composited onto the cream background)
    a = out.getchannel("A").filter(ImageFilter.MinFilter(3))
    out.putalpha(a)
    bbox = a.getbbox()
    if bbox:
        pad = 6
        x0, y0, x1, y1 = bbox
        out = out.crop((max(0, x0 - pad), max(0, y0 - pad),
                        min(out.width, x1 + pad), min(out.height, y1 + pad)))
    return out


def _solidity(pil_img) -> float:
    """Filled area as a fraction of the subject's bounding box.

    A stick figure drawn as thin lines occupies ~0.10-0.30 of its bbox; a
    filled black silhouette / solid torso wedge is ~0.40+. Works on both an
    rembg cutout (uses alpha) and a raw generation on the grey matte (uses
    'darker than the matte').
    """
    import numpy as np
    a = np.asarray(pil_img.convert("RGBA"))
    alpha = a[..., 3]
    lum = a[..., :3].mean(axis=2)
    has_alpha = int(alpha.max()) > 0 and int(alpha.min()) < 255
    if has_alpha:
        subject = alpha > 100                       # rembg cutout: the figure
        ink_px = subject & (lum < 90)               # opaque near-black only
    else:
        subject = np.abs(lum - 138) > 28            # anything unlike the grey matte
        ink_px = lum < 90
    ys, xs = np.where(subject)
    if len(ys) < 50:
        return 0.0
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    area = (y1 - y0) * (x1 - x0)
    return float(ink_px[y0:y1, x0:x1].sum() / area)


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
    for sub in ("bg", "char", "prop", "cutout", "chart", "headline"):
        (a / sub).mkdir(parents=True, exist_ok=True)

    # ---- charts + headlines (both no-API) ----
    if plan.get("charts"):
        from . import charts as chart_mod
        for bid, spec in plan["charts"].items():
            out = a / "chart" / f"{bid}.png"
            if out.exists() and not force:
                continue
            chart_mod.render(spec, out)
            print(f"  chart {bid}  ({spec['type']}, {len(spec['values'])} pts)")
    if plan.get("headlines"):
        from . import headline as hl_mod
        for bid, text in plan["headlines"].items():
            out = a / "headline" / f"{bid}.png"
            if out.exists() and not force:
                continue
            hl_mod.render(text, out)
            print(f"  headline {bid}  ({text!r})")

    cw, ch = config.canvas(script.fmt)
    client = _client()
    cfg = _cfg(script.fmt)

    # ---- backgrounds ----
    for name, sc in plan["scenes"].items():
        out = a / "bg" / f"{name}.png"
        if out.exists() and not force:
            continue
        if not sc["bg"]:
            _flat_bg(sc["color"] or "#ffffff", cw, ch).save(out)
            print(f"  bg {name} (flat {sc['color'] or '#ffffff'})")
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
    # A filled black torso here poisons every pose, so retry until it's line art.
    LINE_LOCK = (
        "The whole figure is thin LINE ART: an open round head (with spiky "
        "hair) plus five separate thin straight lines (spine, 2 arms, 2 legs), "
        "dot hands, and a small skinny necktie. NEVER a filled black body, "
        "NEVER a solid torso wedge, NEVER a silhouette. EXACTLY ONE figure in "
        "the frame -- never two people, never a duplicate or mirror image."
    )
    sheets = {}
    for name, c in plan["characters"].items():
        out = a / "char" / f"_{name}.png"
        if not out.exists() or force:
            prompt = (f"{STYLE_FLOOR}\n{CHAR_FLOOR}\n{LINE_LOCK}\n\n"
                      f"CHARACTER: {c['look']}\n\nDraw a reference sheet: this "
                      f"character full-body, front and 3/4 views, {MATTE_BG}. "
                      f"No other characters, no text.")
            img = _pil_or_none(_generate(client, [prompt], cfg))
            if img is not None and _solidity(img) > 0.10:
                alt = _pil_or_none(_generate(client, [
                    prompt + "\n\nThe last drawing filled the body solid black. "
                    "Redraw the body as ONE THIN LINE, not a shape."], cfg))
                if alt is not None and _solidity(alt) < _solidity(img):
                    img = alt
            if img is None:
                raise RuntimeError(f"char sheet {name}: no image returned")
            img.save(out)
            print(f"  char sheet {name} (solidity {_solidity(img):.2f})")
        sheets[name] = Image.open(out)

    # ---- poses (transparent) ----
    for key, spec in plan["poses"].items():
        out = a / "char" / f"{key}.png"
        if out.exists() and not force:
            continue
        c = plan["characters"][spec["char"]]
        pose_prompt = (
            f"{STYLE_FLOOR}\n{CHAR_FLOOR}\n{LINE_LOCK}\n\nCHARACTER: {c['look']}\n"
            f"POSE / EXPRESSION: {spec['state']}\n\n"
            f"ONLY the single character, whole figure head to feet, centred, "
            f"filling most of the frame vertically, {MATTE_BG}. NO furniture, "
            "NO background objects, NO floor, NO other characters, NO text -- "
            "just the figure (plus a held prop only if the pose names one). "
            "The first image is the reference sheet; stay exactly on-model.")
        img = _pil_or_none(_generate(client, [pose_prompt, sheets[spec["char"]]], cfg))
        if img is None:
            print(f"  pose {key}: no image, using reference sheet crop as fallback")
            img = sheets[spec["char"]]
        cut = _cutout(img, ink=True)
        if _solidity(cut) > 0.13:
            print(f"  pose {key}: body looks filled, retrying once")
            retry = _pil_or_none(_generate(client, [
                pose_prompt + "\n\nThe last try filled the body solid black -- "
                "the body must be ONE THIN LINE.", sheets[spec["char"]]], cfg))
            if retry is not None:
                rcut = _cutout(retry, ink=True)
                if _solidity(rcut) < _solidity(cut):
                    cut = rcut
        cut.save(out)
        print(f"  pose {key}")

    # ---- props (transparent) ----
    any_sheet = next(iter(sheets.values()), None)
    for key, spec in plan["props"].items():
        out = a / "prop" / f"{key}.png"
        if out.exists() and not force:
            continue
        lib = icons.path(key)
        if lib is not None:
            shutil.copyfile(lib, out)
            print(f"  prop {key}  (icon library)")
            continue
        contents = [
            f"{STYLE_FLOOR}\n\nDraw a single {spec['name']} as a flat 2-D doodle "
            "in the EXACT same drawing style as the reference image: the same "
            "thick even black ink outline and weight, at most one or two flat "
            "solid fill colours, NO 3-D, NO shading, NO gloss or highlights, NO "
            f"realism. One object only, centred, {MATTE_BG}. No text, no hands, "
            "no character, no ground."]
        if any_sheet is not None:
            contents.append(any_sheet)
        _cutout(_pil_from(_generate(client, contents, cfg)), ink=True).save(out)
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
