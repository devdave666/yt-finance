"""Generate the fixed prop-icon library ONCE, commit the PNGs, never regenerate.

Props were the weak spot -- Nano Banana renders objects too realistically and
inconsistently when called per-video. Instead we generate a curated flat-icon
set a single time, review it, commit it, and the pipeline just composites these.

    python tools/build_icons.py                 # make any missing icons
    python tools/build_icons.py --force coin clock   # redo specific ones
    python tools/build_icons.py --contact       # write a review grid

Output: assets/icons/<name>.png  (transparent, trimmed)
"""
import sys
from io import BytesIO
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from google import genai  # noqa: E402
from google.genai import types  # noqa: E402
from PIL import Image  # noqa: E402

from stickfin import assets, config  # noqa: E402

OUT = Path("assets/icons")

STYLE = (
    "Flat 2-D icon of {desc}, drawn like a simple marker doodle. One bold "
    "uniform black outline (about 6 px) directly around the shape, filled with "
    "flat solid colours only ({colours}); no gradient, no shading, no "
    "highlights, no 3-D, no perspective. NO white or coloured border/halo/"
    "sticker outline around the icon -- just the black line art. The single "
    "object centred with generous margin on a pure flat #8a8a8a grey "
    "background. Nothing else. No text label, no drop shadow, no ground."
)

ICONS = {
    "coin": ("a round coin stamped with a dollar sign", "gold / yellow"),
    "coins-stack": ("a short neat stack of round coins", "gold / yellow"),
    "cash": ("a fanned stack of paper banknotes", "green"),
    "wallet": ("a simple closed bi-fold wallet", "brown"),
    "piggy-bank": ("a piggy bank in side view with a coin slot on top", "pink"),
    "chart-up": ("a line graph whose line climbs to an arrowhead at the top right", "black line, green arrow"),
    "chart-down": ("a line graph whose line drops to an arrowhead at the bottom right", "black line, red arrow"),
    "bars-up": ("a bar chart of three bars increasing in height from left to right", "blue bars"),
    "bars-down": ("a bar chart of three bars decreasing in height from left to right", "grey bars"),
    "arrow-up": ("a single bold arrow pointing straight up", "green"),
    "arrow-down": ("a single bold arrow pointing straight down", "red"),
    "snowball": ("a big round ball of packed snow sitting still, its surface dimpled, a few small snow chunks flaking off the bottom", "white with pale blue-grey shadow"),
    "clock": ("a round analogue wall clock with an hour and minute hand", "white face, black hands"),
    "hourglass": ("an hourglass with sand in the lower bulb", "brown frame, tan sand"),
    "calendar": ("a tear-off desk calendar page showing a grid", "white page, red header"),
    "bank": ("a classic bank building with columns and a triangular pediment", "light grey"),
    "credit-card": ("a plain credit card with a chip and a magnetic stripe", "blue"),
    "safe": ("a small closed vault door with a round combination dial", "dark grey"),
    "scale": ("a level two-pan balance scale", "black"),
    "shield": ("a smooth simple heraldic shield shape with one bold white check mark centred on it", "solid blue shield, white check, black outline"),
    "padlock": ("a closed padlock", "yellow body, grey shackle"),
    "question": ("a single large question mark", "blue"),
    "lightbulb": ("a lit lightbulb", "yellow"),
    "warning": ("a triangular warning sign with an exclamation mark", "yellow, black mark"),
    "house": ("a simple house with one door and one window", "beige walls, red roof"),
    "target": ("a bullseye target with an arrow stuck in the centre", "red and white rings"),
    "percent": ("a large percent sign", "black"),
    "handshake": ("two hands shaking", "black outline, light skin"),
    "receipt": ("a long paper receipt with lines of text and a total", "white paper"),
    "magnet": ("a horseshoe magnet", "red and grey"),
}


def _client():
    return genai.Client(vertexai=True, project=config.GCP_PROJECT,
                        location=config.IMAGE_LOCATION)


def _gen(client, cfg, desc, colours):
    from google.genai import errors
    prompt = STYLE.format(desc=desc, colours=colours)
    for attempt in range(5):
        try:
            r = client.models.generate_content(model=config.IMAGE_MODEL,
                                               contents=[prompt], config=cfg)
            for c in r.candidates or []:
                for p in c.content.parts or []:
                    inl = getattr(p, "inline_data", None)
                    if inl and getattr(inl, "data", None):
                        return Image.open(BytesIO(inl.data)).convert("RGB")
            raise RuntimeError("no image")
        except errors.ClientError as e:
            if "429" not in str(e) or attempt == 4:
                raise
            import time
            time.sleep(20 * 2 ** attempt)


def main(argv):
    force = "--force" in argv
    contact = "--contact" in argv
    picks = [a for a in argv if not a.startswith("--")]
    OUT.mkdir(parents=True, exist_ok=True)

    if contact:
        names = sorted(p.stem for p in OUT.glob("*.png"))
        cols = 6
        rows = (len(names) + cols - 1) // cols
        sheet = Image.new("RGB", (cols * 220, rows * 220), (200, 200, 200))
        from PIL import ImageDraw
        d = ImageDraw.Draw(sheet)
        for i, n in enumerate(names):
            im = Image.open(OUT / f"{n}.png").convert("RGBA")
            im.thumbnail((190, 170))
            x, y = (i % cols) * 220, (i // cols) * 220
            sheet.paste(im, (x + 15, y + 10), im)
            d.text((x + 10, y + 195), n, fill=(0, 0, 0))
        sheet.save("assets/icons_contact.png")
        print("wrote assets/icons_contact.png")
        return

    client = _client()
    cfg = types.GenerateContentConfig(
        image_config=types.ImageConfig(aspect_ratio="1:1"))
    todo = picks or list(ICONS)
    for name in todo:
        if name not in ICONS:
            print(f"  ? unknown icon {name}")
            continue
        out = OUT / f"{name}.png"
        if out.exists() and not force:
            continue
        desc, colours = ICONS[name]
        cut = assets._cutout(_gen(client, cfg, desc, colours))
        cut.save(out)
        print(f"  {name}")


if __name__ == "__main__":
    main(sys.argv[1:])
