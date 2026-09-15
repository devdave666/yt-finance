"""Cut a grid sticker-sheet (poses on a flat magenta/pink key) into individual
transparent, upscaled pose PNGs.

Why not rembg here: rembg is an ML background-segmentation model trained on
real photos. It doesn't know this is a flat colour key, so at every
anti-aliased edge (where the art's black outline blends into the pink
background over 1-3px) it produces a partial alpha WITHOUT correcting the
RGB colour underneath -- the pixel is still e.g. (108, 5, 116), a real blend
of black ink and pink background. Composited onto anything else, that
partial-alpha pixel still contributes its pink tint -> a visible pink
fringe/halo around every figure, worst on hair and dark clothing edges where
the blend run is longest. Confirmed on the actual sheets: the outline-to-bg
edge is a smooth linear ramp from ~(242,4,243) pink down to near-black, not a
hard cut.

The fix is a proper chroma-key with spill suppression: compute alpha from
distance to the known flat background colour, then UNMIX the RGB at partial
alpha (divide out the background's contribution) so the recovered colour is
what the ink would have been at full opacity, not a pink-diluted blend. This
also needs no ML model and is exact for a flat synthetic key.

Usage:
    python -m tools.cut_stickers assets/char_library/sheets/host__sheet_gestures.png \\
        --rows 2 --cols 4 --out assets/char_library/host \\
        --names hands-clasped,thumbs-up,ta-da-open,shrug-uncertain,shocked-hands-up,chin-thinking,point-up-idea,arms-crossed-serious
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter


def _bg_sample(arr: np.ndarray, border: int = 12) -> np.ndarray:
    """Median colour of the sheet's outer border -- the flat key colour."""
    h, w, _ = arr.shape
    strip = np.concatenate([
        arr[:border].reshape(-1, 3),
        arr[-border:].reshape(-1, 3),
        arr[:, :border].reshape(-1, 3),
        arr[:, -border:].reshape(-1, 3),
    ])
    return np.median(strip, axis=0)


def chroma_key_cutout(img: Image.Image, bg: np.ndarray | None = None,
                       candidate_thresh: float = 0.05) -> Image.Image:
    """Vlahos-style flat-colour chroma key with spill suppression -> clean RGBA.

    Colour-likeness to the key alone is not enough to decide "background":
    a pastel object (a piggy bank's pink body, say) can score nearly as
    magenta-like as a real edge pixel by colour alone, and a per-pixel
    threshold either keys out the object's fill or fails to fully clear the
    real edge ramp -- the two cases overlap in colour-distance and can't be
    told apart by colour alone.

    What actually distinguishes them is TOPOLOGY: real background is
    connected all the way out to the sheet's border through other
    background-scored pixels; an enclosed fill of a similar hue is walled
    off by the black ink outline drawn around it (which scores nothing like
    the key). So: find pixels that score as background-ish by colour, label
    connected components, and keep only the components touching the image
    border as the TRUE background -- any same-hue island sealed inside ink
    stays fully opaque regardless of its raw colour score.
    """
    from scipy import ndimage
    arr = np.asarray(img.convert("RGB"), dtype=np.float32)
    if bg is None:
        bg = _bg_sample(arr)
    bg = np.asarray(bg, dtype=np.float32)
    bg_keyness = float(min(bg[0], bg[2]) - bg[1])

    r, g, b = arr[..., 0], arr[..., 1], arr[..., 2]
    keyness = np.clip((np.minimum(r, b) - g) / max(bg_keyness, 1e-6), 0.0, 1.0)

    candidate = keyness > candidate_thresh
    labels, n = ndimage.label(candidate, structure=np.ones((3, 3)))
    border_labels = set(np.unique(labels[0, :])) | set(np.unique(labels[-1, :])) \
        | set(np.unique(labels[:, 0])) | set(np.unique(labels[:, -1]))
    border_labels.discard(0)

    # A background-coloured speck sealed INSIDE the art (e.g. the sliver of
    # magenta the source sheet left inside a curled pig's tail) is candidate
    # by colour but, like the border-connected mass, isn't a deliberate fill
    # -- it's a small gap the background shows through, same failure shape
    # as the rembg small-hole case elsewhere in this pipeline. A real
    # enclosed fill (the pig's whole pink body) is orders of magnitude
    # bigger, so a small-area cutoff separates the two without needing to
    # know anything about the actual subject.
    counts = np.bincount(labels.ravel()) if n else np.array([0])
    small_bg = {lbl for lbl in range(1, n + 1)
                if lbl not in border_labels and counts[lbl] < max(30, int(0.01 * labels.size))}

    is_bg = np.isin(labels, list(border_labels | small_bg)) if (border_labels or small_bg) \
        else np.zeros_like(candidate)

    alpha = np.where(is_bg, 1.0 - keyness, 1.0)
    key_amt = np.where(is_bg, keyness, 0.0)

    # Flat-field grain (a few RGB levels of jitter even on "solid"
    # background) leaves a faint nonzero alpha across the whole background
    # once run through the ratio above -- invisible as a colour haze but
    # enough stray non-zero alpha to blow the tight-crop bbox out to the
    # full cell. Safe to hard-snap now (unlike a plain colour-distance
    # approach): the topology gate above already means only genuine
    # background pixels can have sub-1.0 alpha here, so this can't eat into
    # a real semi-transparent edge that matters.
    alpha = np.where(alpha < 0.08, 0.0, alpha)

    safe_a = np.clip(alpha, 0.06, 1.0)[..., None]
    fg = (arr - key_amt[..., None] * bg) / safe_a
    fg = np.clip(fg, 0, 255)

    out = np.dstack([fg, alpha[..., None] * 255.0]).astype(np.uint8)
    return Image.fromarray(out, "RGBA")


def split_grid(img: Image.Image, rows: int, cols: int) -> list[Image.Image]:
    w, h = img.size
    cw, ch = w // cols, h // rows
    cells = []
    for r in range(rows):
        for c in range(cols):
            cells.append(img.crop((c * cw, r * ch, (c + 1) * cw, (r + 1) * ch)))
    return cells


def tight_crop(rgba: Image.Image, pad: int = 8) -> Image.Image:
    bbox = rgba.getchannel("A").getbbox()
    if not bbox:
        return rgba
    x0, y0, x1, y1 = bbox
    return rgba.crop((max(0, x0 - pad), max(0, y0 - pad),
                       min(rgba.width, x1 + pad), min(rgba.height, y1 + pad)))


def upscale(rgba: Image.Image, target_h: int = 1600) -> Image.Image:
    """Lanczos upscale to a reels-usable size + a mild unsharp pass to keep
    the flat-vector line art crisp (Lanczos alone softens hard ink edges when
    scaling past ~2x)."""
    if rgba.height >= target_h:
        return rgba
    scale = target_h / rgba.height
    new_size = (round(rgba.width * scale), round(rgba.height * scale))
    up = rgba.resize(new_size, Image.LANCZOS)
    rgb = up.convert("RGB").filter(
        ImageFilter.UnsharpMask(radius=2, percent=120, threshold=2))
    out = rgb.convert("RGBA")
    out.putalpha(up.getchannel("A"))
    return out


def process_sheet(path: Path, rows: int, cols: int, out_dir: Path,
                   names: list[str] | None, target_h: int, pad: int) -> None:
    sheet = Image.open(path).convert("RGB")
    cells = split_grid(sheet, rows, cols)
    if names and len(names) != len(cells):
        raise ValueError(f"{len(names)} names given but grid has {len(cells)} cells")
    out_dir.mkdir(parents=True, exist_ok=True)
    for i, cell in enumerate(cells):
        cut = chroma_key_cutout(cell)
        cut = tight_crop(cut, pad=pad)
        cut = upscale(cut, target_h=target_h)
        name = names[i] if names else f"r{i // cols}_c{i % cols}"
        out_path = out_dir / f"{name}.png"
        cut.save(out_path)
        print(f"  {out_path}  ({cut.width}x{cut.height})")


def process_character_sheets(sheets: list[tuple[Path, list[str]]], rows: int, cols: int,
                              out_dir: Path, target_h: int, pad: int) -> None:
    """Cut every pose for ONE character across multiple sheets to a SHARED
    crop box, not each pose's own independent tight bbox.

    Per-pose independent tight-cropping breaks the compositor: the layout
    solver fits a character into its on-screen slot by
    min(region_w/asset_w, region_h/asset_h), so two poses with different
    width:height ratios (arms-crossed = narrow, arms-flung-wide = wide) --
    both perfectly reasonable crops of their own content -- end up scaled to
    DIFFERENT apparent heights even in the identical region (confirmed on a
    real render: two poses in the same two-character shot came out 600px vs
    755px tall, a pure artifact of crop shape, not anything about the scene).
    Cropping every pose in this character's set to the union of all their
    bboxes (i.e. a box wide/tall enough for the widest and tallest pose, with
    every OTHER pose sitting inside it with transparent margin) gives every
    pose the identical aw/ah ratio, so the fit-by-min-axis math produces the
    same apparent figure height regardless of which specific pose is on
    screen -- matching how the old live-per-video generation implicitly
    behaved (every pose rendered on the same fixed-aspect canvas).
    """
    sheet_cells: list[tuple[Image.Image, str]] = []
    for path, names in sheets:
        sheet = Image.open(path).convert("RGB")
        cells = split_grid(sheet, rows, cols)
        if len(names) != len(cells):
            raise ValueError(f"{path}: {len(names)} names given but grid has {len(cells)} cells")
        cuts = [chroma_key_cutout(c) for c in cells]
        sheet_cells.extend(zip(cuts, names))

    x0 = y0 = 10**9
    x1 = y1 = -1
    for cut, _ in sheet_cells:
        bbox = cut.getchannel("A").getbbox()
        if not bbox:
            continue
        bx0, by0, bx1, by1 = bbox
        x0, y0 = min(x0, bx0), min(y0, by0)
        x1, y1 = max(x1, bx1), max(y1, by1)
    x0, y0 = max(0, x0 - pad), max(0, y0 - pad)
    cw, ch = sheet_cells[0][0].size
    x1, y1 = min(cw, x1 + pad), min(ch, y1 + pad)

    out_dir.mkdir(parents=True, exist_ok=True)
    for cut, name in sheet_cells:
        framed = cut.crop((x0, y0, x1, y1))
        framed = upscale(framed, target_h=target_h)
        out_path = out_dir / f"{name}.png"
        framed.save(out_path)
        print(f"  {out_path}  ({framed.width}x{framed.height})")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("sheet", type=Path)
    ap.add_argument("--rows", type=int, required=True)
    ap.add_argument("--cols", type=int, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--names", type=str, default="",
                     help="comma-separated pose names, left-to-right top-to-bottom")
    ap.add_argument("--target-h", type=int, default=1600)
    ap.add_argument("--pad", type=int, default=8)
    args = ap.parse_args()
    names = [n.strip() for n in args.names.split(",") if n.strip()] or None
    process_sheet(args.sheet, args.rows, args.cols, args.out, names,
                  args.target_h, args.pad)


if __name__ == "__main__":
    main()
