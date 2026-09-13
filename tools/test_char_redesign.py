"""One-off: generate the redesigned host reference sheet + a few sample poses
so the new look can be reviewed before it's wired into the daily pipeline.

Not part of any pipeline stage -- run manually:
    python -m tools.test_char_redesign
"""
from pathlib import Path

from stickfin import assets as A
from stickfin import generate as G

OUT = Path("build/_char_test")
OUT.mkdir(parents=True, exist_ok=True)

IDENTITY_LOCK = (
    "The figure matches the reference sheet's exact outfit, colour palette, "
    "hairstyle, and proportions every time. EXACTLY ONE whole figure, "
    "head-to-shoes, in the frame -- never two people, never a duplicate, "
    "never a stray extra limb. NEVER a flat black silhouette with no "
    "clothing colour or detail visible."
)

client = A._client()
cfg = A._cfg("short")

sheet_prompt = (
    f"{A.STYLE_FLOOR}\n{A.CHAR_FLOOR}\n{IDENTITY_LOCK}\n\n"
    f"CHARACTER: {G.HOST_LOOK}\n\nDraw a reference sheet: ONE single full-body "
    f"figure only, three-quarter turned view (facing slightly to its right, "
    f"matching how every pose will be drawn), {A.MATTE_BG}. EXACTLY ONE "
    f"figure in the frame -- no second view, no front-view copy standing "
    f"beside it, no other characters, no text."
)
sheet_img = A._pil_from(A._generate(client, [sheet_prompt], cfg))
sheet_img.save(OUT / "host_sheet.png")
print(f"saved {OUT / 'host_sheet.png'}  (solidity {A._solidity(sheet_img):.2f})")

poses = {
    "confident_arms_crossed": "standing, arms crossed, confident, neutral expression",
    "pointing_alarmed": "pointing to the right, alarmed, eyebrows raised",
    "shocked_arms_up": "arms up in shock, mouth wide open in a shout",
}
for key, state in poses.items():
    pose_prompt = (
        f"{A.STYLE_FLOOR}\n{A.CHAR_FLOOR}\n{IDENTITY_LOCK}\n\n"
        f"CHARACTER: {G.HOST_LOOK}\nPOSE / EXPRESSION: {state}\n"
        "Draw the whole face for that expression -- eyes, both eyebrows, and "
        "a mouth. The mouth must be visible.\n"
        "DIRECTION: the figure is a presenter addressing something just off "
        "to its RIGHT -- body and head turned slightly right, gaze to the "
        "right.\n\n"
        f"ONLY the single character, whole figure head to feet, centred, "
        f"filling most of the frame vertically, {A.MATTE_BG}. NO furniture, "
        "NO background objects, NO floor, NO other characters, NO text. "
        "The first image is the reference sheet; stay exactly on-model."
    )
    pose_img = A._pil_from(A._generate(client, [pose_prompt, sheet_img], cfg))
    cut = A._cutout(pose_img, ink=False)
    cut.save(OUT / f"pose_{key}.png")
    score = A._pose_defects(cut)
    print(f"saved {OUT / f'pose_{key}.png'}" + (f"  [!! {score[2]}]" if score[2] else ""))

second_sheet_prompt = (
    f"{A.STYLE_FLOOR}\n{A.CHAR_FLOOR}\n{IDENTITY_LOCK}\n\n"
    f"CHARACTER: {G.SECOND_LOOK}\n\nDraw a reference sheet: ONE single "
    f"full-body figure only, three-quarter turned view (facing slightly to "
    f"its right, matching how every pose will be drawn), {A.MATTE_BG}. "
    f"EXACTLY ONE figure in the frame -- no second view, no front-view copy "
    f"standing beside it, no other characters, no text."
)
second_sheet_img = A._pil_from(A._generate(client, [second_sheet_prompt], cfg))
second_sheet_img.save(OUT / "second_sheet.png")
print(f"saved {OUT / 'second_sheet.png'}  (solidity {A._solidity(second_sheet_img):.2f})")
