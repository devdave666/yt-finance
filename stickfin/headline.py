"""Render the hook headline -- the big scroll-stopping text on beat 1.

A short punch (a number, a challenge, a shock: "$1,000,000", "YOU'RE LOSING
MONEY", "$35. EVERY TIME.") drawn huge in the Anti Broke brand style: heavy
condensed caps, white with a thick black stroke, the money/number parts in
brand green, a rough green underline. Transparent PNG, composited as a hero
layer with the host shrunk into a corner.

No API -- just PIL.
"""
from __future__ import annotations

import re
from pathlib import Path

GREEN = (124, 179, 66)
WHITE = (250, 250, 250)
INK = (18, 18, 18)


def _font(size: int):
    from PIL import ImageFont
    for name in ("impact.ttf", "ariblk.ttf", "arialbd.ttf", "Anton-Regular.ttf",
                 "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except Exception:
            continue
    return ImageFont.load_default()


def _key(w: str) -> bool:
    return bool(re.search(r"[\d$%]", w))


def render(text: str, out: Path, width_px: int = 1180) -> Path:
    from PIL import Image, ImageDraw

    words = text.upper().split()
    if not words:
        words = ["?"]
    # lay out on up to 3 lines, ~14 chars each
    lines, cur = [], ""
    for w in words:
        if cur and len(cur) + 1 + len(w) > 15:
            lines.append(cur)
            cur = w
        else:
            cur = f"{cur} {w}".strip()
    if cur:
        lines.append(cur)
    lines = lines[:3]

    meas = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

    def _row_w(line: str, font) -> float:
        return sum(meas.textlength(w + " ", font=font) for w in line.split())

    # width_px/(chars*0.62) is only a starting guess -- bold caps run wider
    # than that for some text, and PIL silently draws (and clips) past the
    # canvas edge rather than erroring, so a bad guess baked a clipped
    # headline right into the asset. Measure for real and shrink to fit.
    avail_w = width_px * 0.88
    size = max(70, min(190, int(width_px / (max(len(l) for l in lines) * 0.62))))
    font = _font(size)
    max_row_w = max(_row_w(l, font) for l in lines)
    for _ in range(3):
        if max_row_w <= avail_w:
            break
        size = max(36, int(size * avail_w / max_row_w))
        font = _font(size)
        max_row_w = max(_row_w(l, font) for l in lines)

    pad = int(size * 0.5)
    lh = int(size * 1.16)
    canvas_h = lh * len(lines) + pad * 2 + int(size * 0.35)
    # size the canvas to what the text actually measures (+ margin for the
    # stroke outline), not just the nominal width_px -- belt-and-suspenders
    # so no measurement error can clip a glyph again
    canvas_w = max(width_px, int(max_row_w + pad * 2))
    img = Image.new("RGBA", (canvas_w, canvas_h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    stroke = max(6, size // 12)

    y = pad
    line_words = [ln.split(" ") for ln in lines]
    text_l, text_r = canvas_w, 0.0
    for lw in line_words:
        widths = [d.textlength(w + " ", font=font) for w in lw]
        row_w = sum(widths)
        x = (canvas_w - row_w) / 2
        text_l = min(text_l, x)
        text_r = max(text_r, x + row_w - d.textlength(" ", font=font))
        for w, wdt in zip(lw, widths):
            col = GREEN if _key(w) else WHITE
            d.text((x, y), w, font=font, fill=col, stroke_width=stroke, stroke_fill=INK)
            x += wdt
        y += lh

    # rough green underline spanning the actual text block (+ a small overhang)
    uy = pad + lh * len(lines) + int(size * 0.16)
    over = size * 0.12
    d.line([(text_l - over, uy), (text_r + over, uy + int(size * 0.05))],
           fill=GREEN, width=max(8, size // 9))

    img = img.rotate(-3, expand=True, resample=Image.BICUBIC)
    bbox = img.getchannel("A").getbbox()
    if bbox:
        img = img.crop(bbox)
    out.parent.mkdir(parents=True, exist_ok=True)
    img.save(out)
    return out
