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


_VBANDS = {"top": 0.30, "mid": 0.46, "low": 0.64, "bottom": 0.80}


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
    """Return (x, y, w, h) for a prop/cutout.

    `at` is `<h>` or `<h>-<v>` where h in left/center/right and v in
    top/mid/bottom. Vertical bands are chosen to clear the caption strip at the
    top and the character's head.
    """
    cw, ch = config.canvas(fmt)
    aw, ah = asset_wh

    parts = at.split("-")
    vbias = parts[-1] if parts[-1] in _VBANDS else ""
    hname = "-".join(parts[:-1]) if vbias else at

    # scale to a target height, but clamp so a wide icon can't span the figure
    scale = min(ch * obj_scale / ah, cw * config.PROP_MAX_W_FRAC / aw)
    w, h = round(aw * scale), round(ah * scale)

    x = round(_anchor_x(hname, cw) - w / 2)
    y = round(ch * _VBANDS.get(vbias, 0.5) - h / 2)
    return x, y, w, h


def clamp(box: tuple[int, int, int, int], fmt: str) -> tuple[int, int, int, int]:
    cw, ch = config.canvas(fmt)
    x, y, w, h = box
    x = max(-w // 3, min(x, cw - 2 * w // 3))
    y = max(0, min(y, ch - h // 2))
    return x, y, w, h
