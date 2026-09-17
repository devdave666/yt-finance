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

# Fixed split-screen template for a two-character (skit) short: the user's
# own layout sketch -- one shared ASSET band on top (charts/headlines/props),
# a narrow DIALOGUE strip per character directly above their head (captions.py
# positions each speaker's line here instead of a shared top pill), then two
# mirrored, EQUAL-SIZE character slots at the bottom. Character X-ranges sit
# fully inside the existing side-margin safe area on their own, so a pose
# only ever spills toward the centre gap or the other slot, never off-canvas.
_DUO_ASSET_Y = (0.08, 0.46)
# A headline graphic only ever renders on beat 1 (SCHEMA_DOC requires it
# there, nowhere else), and beat 1 in `caption_style: title` is exactly when
# the persistent title-card caption sits top-anchored at MarginV=250 (see
# captions.py's TITLE_CARD_MARGIN_V) -- roughly y=250-430px on a 1920 canvas,
# fraction ~0.13-0.22. _DUO_ASSET_Y's normal 0.08 start put the headline
# graphic's region overlapping that caption directly; a real published short
# shipped with the title card pill sitting on top of the headline text.
# layout.audit()'s collision check only sees composited PNG layers, never
# the burned-in ASS caption, so this class of bug is invisible to it -- fixed
# by giving the headline its own lower start instead, generous enough to
# clear a worst-case 3-line wrapped title card.
_DUO_HEADLINE_Y0 = 0.25
_DUO_DIALOGUE_Y = (0.47, 0.565)
# Height chosen so the WIDEST pose in the char library (1207x1600, an
# arms-flung gesture) still fits its own half-slot width (394px) at the
# height-first scale below, with a little headroom -- not tuned for the
# common case, tuned for the worst one, so the two-character overlap
# fallback in solve() stays a true rare-case safety net instead of firing
# on ordinary pose pairs (it did, routinely, at a taller band: two
# only-moderately-wide poses (488px + 406px) already summed past the 842px
# total safe width and triggered the collision resolver's WORST available
# move -- a big vertical shove that put one character noticeably higher
# than the other, defeating the entire point of this template).
_DUO_CHAR_Y = (0.685, FEET)
_DUO_LEFT_X = (0.11, 0.475)
_DUO_RIGHT_X = (0.525, 0.89)


def _duo_safe(cw: int, ch: int) -> Box:
    """Duo mode's own safe rect. The shared _safe() reserves g['top']=0.27 for
    the old single top caption band -- irrelevant here since dialogue lives in
    the D1/D2 boxes instead, and clamping the asset band against that leftover
    reservation was pushing it down into the character/dialogue zones. Side
    margins (phone cover-crop safety) and the bottom reservation still apply."""
    g = _GEOM["short"]
    x0 = round(g["side"] * cw)
    x1 = round((1 - g["side"]) * cw)
    y0 = round(0.03 * ch)
    y1 = round((1 - g["bottom"]) * ch)
    return x0, y0, x1 - x0, y1 - y0


def duo_dialogue_anchor(fmt: str, side: str) -> tuple[int, int]:
    """Bottom-centre pixel anchor for a speaker's dialogue box (D1 left / D2
    right), directly above their fixed character slot -- captions.py \\pos()s
    a speaker's line here so it reads as coming from them, not a shared
    top-of-screen caption."""
    cw, ch = config.canvas(fmt)
    x0, x1 = _DUO_LEFT_X if side == "left" else _DUO_RIGHT_X
    cx = round((x0 + x1) / 2 * cw)
    y = round(_DUO_DIALOGUE_Y[1] * ch)
    return cx, y


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


def _solve_duo(elements: list[dict], kinds: list[str], cw: int, ch: int,
               safe: Box) -> list[Box]:
    """The fixed two-character template (see _DUO_* above). Characters get a
    dedicated height-first fit here instead of going through the shared
    _fit()'s min(width, height) -- that min() is exactly what made two
    different poses in the identical slot render at different heights (a
    wide gesture pose loses to width, a narrow one doesn't), which is the
    whole reason for this template: BOTH slots share the same region height
    and every pose asset shares the same source pixel height (1600, the
    char-library's upscale target), so height-first alone gives every pose
    the same on-screen height regardless of its own width. A pose wide
    enough to spill past its own slot is left to the shared de-overlap pass
    in solve(), same safety net every other template already relies on.
    """
    chars = [i for i, k in enumerate(kinds) if k == "character"]
    others = [i for i, k in enumerate(kinds) if k != "character"]
    boxes: list[Box | None] = [None] * len(elements)

    char_h = (_DUO_CHAR_Y[1] - _DUO_CHAR_Y[0]) * ch
    for slot, i in zip(("left", "right"), chars):
        aw, ah = elements[i]["wh"]
        s = char_h / ah
        w, h = max(1, round(aw * s)), round(char_h)
        x0, x1 = _DUO_LEFT_X if slot == "left" else _DUO_RIGHT_X
        cx = (x0 + x1) / 2 * cw
        x, y = round(cx - w / 2), round(_DUO_CHAR_Y[1] * ch - h)
        boxes[i] = _clamp((x, y, w, h), safe)

    for j, i in enumerate(others):
        y0 = _DUO_HEADLINE_Y0 if kinds[i] == "headline" else _DUO_ASSET_Y[0]
        region = (0.06, y0, 0.94, _DUO_ASSET_Y[1]) if j == 0 \
            else (0.30, y0, 0.70, _DUO_ASSET_Y[1])
        boxes[i] = _clamp(_fit(elements[i]["wh"], region, cw, ch, "center"), safe)

    return boxes


# --------------------------------------------------------------------------
# public
# --------------------------------------------------------------------------

def solve(elements: list[dict], fmt: str) -> list[Box]:
    """elements: [{"type": ..., "wh": (w, h)}], in draw order. Returns a box each."""
    if not elements:
        return []
    cw, ch = config.canvas(fmt)
    kinds = [e["type"] for e in elements]

    n_chars = sum(1 for k in kinds if k == "character")
    duo = fmt != "wide" and n_chars == 2
    safe = _duo_safe(cw, ch) if duo else _safe(cw, ch, fmt)
    if duo:
        boxes: list[Box] = _solve_duo(elements, kinds, cw, ch, safe)
    else:
        regions = _regions(kinds, fmt)
        boxes = []
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
    duo = fmt != "wide" and sum(1 for k in kinds if k == "character") == 2
    sx, sy, sw, sh = _duo_safe(cw, ch) if duo else _safe(cw, ch, fmt)
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
