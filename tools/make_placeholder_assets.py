"""Offline stand-ins for the Vertex asset stage.

Reads asset_plan.json and writes:
  bg/<scene>.png      opaque colour field with a label
  char/<pose>.png     transparent PNG with a crude coloured stick figure
  prop/<name>.png     transparent PNG with a labelled box
  cutout/<hash>.png   transparent PNG placeholder

Lets the compositor + caption + mux path be verified with zero image spend.

    python tools/make_placeholder_assets.py build/skit-doctor-glasses
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from PIL import Image, ImageDraw  # noqa: E402

PALETTE = [(214, 40, 40), (0, 100, 170), (245, 183, 0), (60, 160, 90), (120, 80, 200)]


def _stick(draw, w, h, color):
    cx, top = w // 2, int(h * 0.12)
    r = int(h * 0.11)
    draw.ellipse([cx - r, top, cx + r, top + 2 * r], outline=(0, 0, 0), width=10, fill=(255, 255, 255))
    hip = int(h * 0.62)
    draw.line([cx, top + 2 * r, cx, hip], fill=(0, 0, 0), width=10)
    draw.line([cx, int(h * 0.42), cx - int(w * 0.28), int(h * 0.5)], fill=(0, 0, 0), width=10)
    draw.line([cx, int(h * 0.42), cx + int(w * 0.28), int(h * 0.48)], fill=(0, 0, 0), width=10)
    draw.line([cx, hip, cx - int(w * 0.22), int(h * 0.95)], fill=(0, 0, 0), width=10)
    draw.line([cx, hip, cx + int(w * 0.22), int(h * 0.95)], fill=(0, 0, 0), width=10)
    draw.rectangle([cx - int(w * 0.18), top + 2 * r, cx + int(w * 0.18), hip], fill=color + (180,))


def main(build_dir: str) -> None:
    bd = Path(build_dir)
    plan = json.loads((bd / "asset_plan.json").read_text())
    a = bd / "assets"
    for sub in ("bg", "char", "prop", "cutout", "chart"):
        (a / sub).mkdir(parents=True, exist_ok=True)

    from stickfin import config
    cw, ch = config.canvas(plan["fmt"])

    from stickfin.assets import _flat_bg
    for name, sc in plan["scenes"].items():
        if sc.get("bg"):
            img = Image.new("RGB", (cw, ch), (238, 234, 226))
            ImageDraw.Draw(img).text((80, 80), f"BG(gen): {name}", fill=(150, 150, 150))
        else:
            img = _flat_bg(sc.get("color") or "#f4efe4", cw, ch)
        img.save(a / "bg" / f"{name}.png")

    for i, (key, spec) in enumerate(plan["poses"].items()):
        img = Image.new("RGBA", (int(cw * 0.55), int(ch * 0.62)), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        _stick(draw, img.width, img.height, PALETTE[i % len(PALETTE)])
        draw.text((10, 6), key[:40], fill=(0, 0, 0, 255))
        img.save(a / "char" / f"{key}.png")

    for key in plan["props"]:
        img = Image.new("RGBA", (360, 360), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rounded_rectangle([20, 20, 340, 340], radius=24, outline=(0, 0, 0, 255),
                            width=8, fill=(255, 255, 255, 210))
        d.text((36, 160), key[:22], fill=(0, 0, 0, 255))
        img.save(a / "prop" / f"{key}.png")

    for key, spec in plan["cutouts"].items():
        if spec.get("kind") == "live":
            continue
        img = Image.new("RGBA", (500, 400), (0, 0, 0, 0))
        d = ImageDraw.Draw(img)
        d.rectangle([10, 10, 490, 390], outline=(200, 40, 40, 255), width=8,
                    fill=(255, 220, 220, 200))
        d.text((28, 180), f"cutout {key}", fill=(120, 0, 0, 255))
        img.save(a / "cutout" / f"{key}.png")

    # charts are real even in the offline path (matplotlib, no API)
    from stickfin import charts as chart_mod
    for bid, spec in plan.get("charts", {}).items():
        chart_mod.render(spec, a / "chart" / f"{bid}.png")

    n = (len(plan["scenes"]) + len(plan["poses"]) + len(plan["props"])
         + len(plan["cutouts"]) + len(plan.get("charts", {})))
    print(f"{n} placeholder assets -> {a}")


if __name__ == "__main__":
    main(sys.argv[1])
