"""Where every element sits on the canvas -- collision-free, on-screen.

`solve(elements, fmt)` takes the shot's layers (each with its real PNG size)
and returns a pixel box per layer, guaranteed to:
  * stay inside the safe area (nothing clipped by the frame edge)
  * keep the top caption band clear
  * not overlap each other (lower-priority elements get moved, then shrunk)

It picks a template from what's in the shot (solo figure / figure + props /
figure + chart / chart-focus / two-hander), resolves each element into its
region preserving aspect ratio, then runs a clamp + de-overlap pass.
"""
from __future__ import annotations

from . import config

Box = tuple[int, int, int, int]          # x, y, w, h  (top-left origin)

# Per-format frame geometry, as fractions of the canvas.
#
#   side   left/right margin
#   top    everything above this is reserved (caption band on 9:16)
#   bottom everything below 1-bottom is reserved (caption band on 16:9)
#   feet   the baseline characters stand on
#   band   which edge the burned-in captions live against
#
# short (9:16): Reels/TikTok/Shorts render a fixed 1080x1920, but most phone
#   screens aren't exactly 9:16 -- the player "cover"-scales to fill the real
#   screen and crops the excess off the LEFT/RIGHT edges (~9% per side on a
#   19.5:9 device). 3.5% wasn't enough; a real short shipped with a headline
#   letter and an arm clipped by exactly this. 11% clears it. Captions ride the
#   top because the platform's own UI (caption, handle, action rail) covers the
#   bottom third.
# wide (16:9): a YouTube player letterboxes rather than cover-cropping, so the
#   aggressive side margin is pure wasted canvas -- 5% is a normal title-safe
#   inset. Captions belong in a bottom lower-third here (that's where a viewer
#   expects subtitles on a landscape player), so the reserved band flips to the
#   bottom and the character baseline lifts to sit clear above it.
#   The wide bottom band is DERIVED from the real subtitle metrics
#   (config.subtitle_band_frac) rather than hand-tuned: a worst-case 3-line
#   caption is 187px tall, and a guessed 0.22 band left it overlapping the
#   artwork by 27px. Deriving it means the two can't drift apart again.
_WIDE_BOTTOM = round(config.subtitle_band_frac(config.FORMATS["wide"][1]), 4)

_GEOM = {
    "short": {"side": 0.11, "top": 0.27, "bottom": 0.05, "feet": 0.95, "band": "top"},
    "wide":  {"side": 0.05, "top": 0.06, "bottom": _WIDE_BOTTOM,
              "feet": round(1 - _WIDE_BOTTOM, 4), "band": "bottom"},
}


def geom(fmt: str | None = None) -> dict:
    return _GEOM.get(fmt or config.DEFAULT_FORMAT, _GEOM["short"])


# back-compat aliases (short-form values; prefer geom(fmt))
SIDE_MARGIN = _GEOM["short"]["side"]
BOTTOM_MARGIN = _GEOM["short"]["bottom"]
CAPTION_BAND = _GEOM["short"]["top"]
FEET = config.CHAR_BASELINE_FRAC

_PRIORITY = {"headline": 4, "chart": 3, "character": 2, "cutout": 1, "prop": 1}


# --------------------------------------------------------------------------
# geometry helpers
# --------------------------------------------------------------------------

def _fit(wh: tuple[int, int], region: tuple[float, float, float, float],
         cw: int, ch: int, anchor: str) -> Box:
    """Scale wh to fit inside `region` (x0,y0,x1,y1 fractions), placed by anchor
    ('bottom' = feet on region bottom-centre, else centred)."""
    rx0, ry0, rx1, ry1 = region
    rw, rh = (rx1 - rx0) * cw, (ry1 - ry0) * ch
    aw, ah = wh
    s = min(rw / aw, rh / ah)
    w, h = max(1, round(aw * s)), max(1, round(ah * s))
    cx = (rx0 + rx1) / 2 * cw
    if anchor == "bottom":
        x, y = round(cx - w / 2), round(ry1 * ch - h)
    else:
        x, y = round(cx - w / 2), round((ry0 + ry1) / 2 * ch - h / 2)
    return x, y, w, h


def _safe(cw: int, ch: int, fmt: str | None = None) -> Box:
    g = geom(fmt)
    x0 = round(g["side"] * cw)
    y0 = round(g["top"] * ch)
    x1 = round((1 - g["side"]) * cw)
    y1 = round((1 - g["bottom"]) * ch)
    return x0, y0, x1 - x0, y1 - y0


def _clamp(box: Box, safe: Box) -> Box:
    x, y, w, h = box
    sx, sy, sw, sh = safe
    if w > sw:
        s = sw / w
        w, h = sw, max(1, round(h * s))
    if h > sh:
        s = sh / h
        h, w = sh, max(1, round(w * s))
    x = min(max(x, sx), sx + sw - w)
    y = min(max(y, sy), sy + sh - h)
    return x, y, w, h


def _overlap(a: Box, b: Box) -> float:
    ax, ay, aw, ah = a
    bx, by, bw, bh = b
    ix = max(0, min(ax + aw, bx + bw) - max(ax, bx))
    iy = max(0, min(ay + ah, by + bh) - max(ay, by))
    inter = ix * iy
    return inter / min(aw * ah, bw * bh) if inter else 0.0


def _separate(mover: Box, fixed: Box, safe: Box) -> Box:
    """Push `mover` the shortest way out of `fixed`, keeping it in `safe`."""
    mx, my, mw, mh = mover
    fx, fy, fw, fh = fixed
    push_r = fx + fw - mx + 8
    push_l = mx + mw - fx + 8
    push_d = fy + fh - my + 8
    push_u = my + mh - fy + 8
    for dx, dy in sorted([(push_r, 0), (-push_l, 0), (0, push_d), (0, -push_u)],
                         key=lambda p: abs(p[0]) + abs(p[1])):
        cand = _clamp((mx + dx, my + dy, mw, mh), safe)
        if _overlap(cand, fixed) < 0.06:
            return cand
    # couldn't clear by moving -- shrink and centre away
    return _clamp((mx, my, round(mw * 0.82), round(mh * 0.82)), safe)


# --------------------------------------------------------------------------
# templates
# --------------------------------------------------------------------------

def _regions_wide(kinds: list[str]) -> list[tuple[float, float, float, float]]:
    """16:9 long-form templates.

    Landscape wants side-by-side, not the stacked "text on top of a figure"
    shapes 9:16 uses: there is far more width than height, so the data owns the
    right two-thirds and the host presents from the left edge. The host's poses
    are drawn facing RIGHT (see assets.py) which is exactly the direction the
    content sits, so the compositor's auto-mirror leaves them alone.
    """
    g = _GEOM["wide"]
    top, feet = g["top"], g["feet"]
    bot = 1 - g["bottom"]

    chars = [i for i, k in enumerate(kinds) if k == "character"]
    charts = [i for i, k in enumerate(kinds) if k == "chart"]
    heads = [i for i, k in enumerate(kinds) if k == "headline"]
    objs = [i for i, k in enumerate(kinds) if k in ("prop", "cutout")]
    R: list = [None] * len(kinds)

    if heads:
        # title card: big text left, host reacting from the right edge
        R[heads[0]] = (0.05, top + 0.03, 0.66, bot - 0.06)
        if chars:
            R[chars[0]] = (0.70, 0.20, 0.97, feet)
        for j, i in enumerate(objs):
            R[i] = (0.08, 0.60, 0.34, feet - 0.02)
    elif charts:
        # the data is the subject; the host is a presenter beside it
        R[charts[0]] = (0.29, top + 0.01, 0.98, bot)
        for j, i in enumerate(charts[1:]):
            R[i] = (0.55, 0.30, 0.98, bot)
        if len(chars) == 1:
            R[chars[0]] = (0.02, 0.30, 0.26, feet)
        elif len(chars) >= 2:
            R[chars[0]] = (0.01, 0.34, 0.20, feet)
            R[chars[1]] = (0.20, 0.34, 0.38, feet)
        for j, i in enumerate(objs):
            R[i] = (0.03, 0.10, 0.24, 0.34)
    elif len(chars) >= 2:
        R[chars[0]] = (0.06, 0.18, 0.36, feet)
        R[chars[1]] = (0.64, 0.18, 0.94, feet)
        for j, i in enumerate(objs):
            R[i] = (0.40, 0.22 + j * 0.20, 0.60, 0.46 + j * 0.20)
    elif objs and chars:
        R[chars[0]] = (0.08, 0.18, 0.38, feet)
        for j, i in enumerate(objs):
            R[i] = (0.48, 0.18 + j * 0.22, 0.80, 0.52 + j * 0.22)
    elif chars:
        R[chars[0]] = (0.37, top + 0.02, 0.63, feet)   # solo, centred
    else:
        for j, i in enumerate(objs):
            R[i] = (0.34, 0.18 + j * 0.22, 0.66, 0.54 + j * 0.22)

    return [r if r else (0.35, 0.25, 0.65, 0.65) for r in R]


def _regions(kinds: list[str], fmt: str) -> list[tuple[float, float, float, float]]:
    """A target region per element, same order as `kinds`."""
    if fmt == "wide":
        return _regions_wide(kinds)

    chars = [i for i, k in enumerate(kinds) if k == "character"]
    charts = [i for i, k in enumerate(kinds) if k == "chart"]
    heads = [i for i, k in enumerate(kinds) if k == "headline"]
    objs = [i for i, k in enumerate(kinds) if k in ("prop", "cutout")]
    R: list = [None] * len(kinds)

    if heads:
        # the hook headline owns the top half; the figure reacts from a corner
        R[heads[0]] = (0.06, CAPTION_BAND + 0.02, 0.94, 0.58)
        if chars:
            R[chars[0]] = (0.58, 0.56, 0.98, FEET)
        for i in objs:
            R[i] = (0.04, 0.60, 0.42, FEET)
    elif charts:
        # chart dominates the upper stage; figure shrinks to a presenter
        R[charts[0]] = (0.05, CAPTION_BAND + 0.035, 0.95, 0.62)
        for j, i in enumerate(charts[1:]):
            R[i] = (0.30, 0.30, 0.96, 0.55)          # stacked (rare)
        if len(chars) == 1:
            R[chars[0]] = (0.02, 0.52, 0.37, FEET)   # bottom-left presenter
        elif len(chars) == 2:
            R[chars[0]] = (0.02, 0.58, 0.26, FEET)
            R[chars[1]] = (0.74, 0.58, 0.98, FEET)
        for j, i in enumerate(objs):
            R[i] = (0.70, 0.66 + j * 0.16, 0.98, 0.82 + j * 0.16)
    elif len(chars) == 2:
        R[chars[0]] = (0.02, 0.34, 0.40, FEET)
        R[chars[1]] = (0.58, 0.34, 0.98, FEET)
        for j, i in enumerate(objs):
            R[i] = (0.34, 0.30 + j * 0.14, 0.66, 0.46 + j * 0.14)
    elif objs and chars:
        R[chars[0]] = (0.02, 0.34, 0.44, FEET)
        for j, i in enumerate(objs):
            R[i] = (0.60, 0.44 + j * 0.18, 0.98, 0.66 + j * 0.18)
    elif chars:
        R[chars[0]] = (0.18, 0.30, 0.82, FEET)       # solo, centred
    else:
        for j, i in enumerate(objs):
            R[i] = (0.15, 0.34 + j * 0.2, 0.85, 0.62 + j * 0.2)

    return [r if r else (0.2, 0.4, 0.8, 0.7) for r in R]


# --------------------------------------------------------------------------
# public
# --------------------------------------------------------------------------

def solve(elements: list[dict], fmt: str) -> list[Box]:
    """elements: [{"type": ..., "wh": (w, h)}], in draw order. Returns a box each."""
    if not elements:
        return []
    cw, ch = config.canvas(fmt)
    safe = _safe(cw, ch, fmt)
    kinds = [e["type"] for e in elements]
    regions = _regions(kinds, fmt)

    boxes: list[Box] = []
    for e, region in zip(elements, regions):
        anchor = "bottom" if e["type"] == "character" else "center"
        boxes.append(_clamp(_fit(e["wh"], region, cw, ch, anchor), safe))

    # de-overlap: keep higher priority fixed, move/shrink the rest
    order = sorted(range(len(boxes)), key=lambda i: -_PRIORITY.get(kinds[i], 0))
    for _ in range(6):
        moved = False
        for a in range(len(order)):
            for b in range(a + 1, len(order)):
                ia, ib = order[a], order[b]
                if _overlap(boxes[ia], boxes[ib]) > 0.10:
                    boxes[ib] = _separate(boxes[ib], boxes[ia], safe)
                    moved = True
        if not moved:
            break
    return boxes


def audit(kinds: list[str], boxes: list[Box], fmt: str,
          overlap_tol: float = 0.10) -> list[str]:
    """Prove a solved shot is actually clean: nothing outside the safe area,
    nothing meaningfully overlapping anything else.

    solve() *tries* to guarantee both, but _separate()'s last resort just
    shrinks by 0.82 and returns -- which can still leave an overlap when a
    frame is genuinely too crowded. This is the check that says so out loud
    instead of shipping it, and it's what QA gates on.
    """
    cw, ch = config.canvas(fmt)
    sx, sy, sw, sh = _safe(cw, ch, fmt)
    problems: list[str] = []

    for kind, (x, y, w, h) in zip(kinds, boxes):
        if x < sx - 1 or y < sy - 1 or x + w > sx + sw + 1 or y + h > sy + sh + 1:
            problems.append(
                f"{kind} at ({x},{y},{w}x{h}) escapes the safe area "
                f"({sx},{sy},{sw}x{sh})")
        if x < 0 or y < 0 or x + w > cw or y + h > ch:
            problems.append(f"{kind} at ({x},{y},{w}x{h}) is off-canvas ({cw}x{ch})")

    for a in range(len(boxes)):
        for b in range(a + 1, len(boxes)):
            ov = _overlap(boxes[a], boxes[b])
            if ov > overlap_tol:
                problems.append(
                    f"{kinds[a]} and {kinds[b]} overlap by {ov:.0%}")
    return problems
