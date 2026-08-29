"""Where each layer sits on the canvas.

Characters stand on a shared baseline, scaled to a fraction of canvas height,
placed at their anchor column (left / center / right). Props and cutouts are
scaled to a fraction of canvas height and centered on their anchor point.
Everything here is deterministic -- the compositor just applies the numbers.
"""
from __future__ import annotations

from . import config


def _anchor_x(name: str, w: int) -> float:
    return config.ANCHOR_X.get(name, 0.5) * w


def character_box(anchor: str, char_scale: float, asset_wh: tuple[int, int],
                  fmt: str) -> tuple[int, int, int, int]:
    """Return (x, y, w, h) for a character asset, feet on the baseline."""
    cw, ch = config.canvas(fmt)
    aw, ah = asset_wh
    target_h = ch * config.CHAR_HEIGHT_FRAC * char_scale
    scale = target_h / ah
    w, h = round(aw * scale), round(ah * scale)
    x = round(_anchor_x(anchor, cw) - w / 2)
    y = round(ch * config.CHAR_BASELINE_FRAC - h)
    return x, y, w, h


def object_box(at: str, obj_scale: float, asset_wh: tuple[int, int],
               fmt: str) -> tuple[int, int, int, int]:
    """Return (x, y, w, h) for a prop/cutout, centered on its anchor.

    `at` may be an anchor name (left/center/right) or 'name-top' /
    'name-bottom' to bias vertically.
    """
    cw, ch = config.canvas(fmt)
    aw, ah = asset_wh
    target_h = ch * obj_scale
    scale = target_h / ah
    w, h = round(aw * scale), round(ah * scale)

    base, _, vbias = at.partition("-")
    x = round(_anchor_x(base, cw) - w / 2)
    if vbias == "top":
        y = round(ch * 0.14)
    elif vbias == "bottom":
        y = round(ch * 0.94 - h)
    else:
        y = round(ch * 0.52 - h / 2)
    return x, y, w, h


def clamp(box: tuple[int, int, int, int], fmt: str) -> tuple[int, int, int, int]:
    cw, ch = config.canvas(fmt)
    x, y, w, h = box
    x = max(-w // 3, min(x, cw - 2 * w // 3))
    y = max(0, min(y, ch - h // 2))
    return x, y, w, h
