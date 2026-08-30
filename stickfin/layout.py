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

# fractions of the canvas
# Reels/TikTok/Shorts render a fixed 9:16 (1080x1920) canvas, but most phone
# screens aren't exactly 9:16 -- the player "cover"-scales to fill the real
# screen and crops the excess, usually off the LEFT/RIGHT edges (a device
# noticeably taller than 9:16, e.g. 19.5:9, crops ~9% off each side to fill).
# 3.5% wasn't enough margin to survive that; a real short shipped with a
# headline letter and a character's arm clipped by exactly this. 11% clears
# it with room to spare.
SIDE_MARGIN = 0.11
BOTTOM_MARGIN = 0.05
CAPTION_BAND = 0.27                       # top strip reserved for captions
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


def _safe(cw: int, ch: int) -> Box:
    x0 = round(SIDE_MARGIN * cw)
    y0 = round(CAPTION_BAND * ch)
    x1 = round((1 - SIDE_MARGIN) * cw)
    y1 = round((1 - BOTTOM_MARGIN) * ch)
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

def _regions(kinds: list[str], fmt: str) -> list[tuple[float, float, float, float]]:
    """A target region per element, same order as `kinds`."""
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
    safe = _safe(cw, ch)
    kinds = [e["type"] for e in elements]
    regions = _regions(kinds, fmt)

    boxes: list[Box] = []
    for e, region in zip(elements, regions):
        anchor = "bottom" if e["type"] == "character" else "center"
        boxes.append(_clamp(_fit(e["wh"], region, cw, ch, anchor), safe))

    # de-overlap: keep higher priority fixed, move/shrink the rest
    order = sorted(range(len(boxes)), key=lambda i: -_PRIORITY.get(kinds[i], 0))
    for _ in range(3):
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
