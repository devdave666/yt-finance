"""Render a data chart from the script's own numbers -- clean, minimal, on-brand.

The generator may attach a `chart` spec to a data-heavy beat instead of a prop
icon; this turns it into a transparent PNG the compositor drops in like any
other layer. No external data, no screenshots -- just the figures the script
already states, drawn in the paper-doodle house style.

spec = {
  "type":   "bar" | "hbar" | "line",
  "title":  "short chart title" | "",
  "labels": ["2018", "2019", ...],
  "values": [120, 260, ...],
  "unit":   "$B" | "%" | "" ,          # appended to value labels
  "highlight": <index> | null,          # gets the accent colour + a red ring
  "note":   "one short callout" | ""
}
"""
from __future__ import annotations

from pathlib import Path

INK = "#181818"
SLATE = "#9aa3ac"
ACCENT = "#7CB342"      # brand green, matches the caption highlight
RED = "#e0362c"         # kept for the "look here" annotation ring only
PAPER = "#00000000"     # transparent


def _wrap_label(text: str, max_chars: int = 8) -> str:
    """Stack a long bar label onto multiple short lines so neighbours can't
    collide. Breaks on spaces only -- never mid-word."""
    import textwrap
    return "\n".join(textwrap.wrap(str(text), max_chars, break_long_words=False)) or str(text)


def _fmt(v: float, unit: str) -> str:
    s = f"{v:,.0f}" if abs(v) >= 100 or float(v).is_integer() else f"{v:,.1f}"
    if unit.startswith("$"):
        return f"${s}{unit[1:]}"
    return f"{s}{unit}"


def _ease(p: float) -> float:
    """ease-out cubic -- fast start, soft landing."""
    p = min(max(p, 0.0), 1.0)
    return 1 - (1 - p) ** 3


def _elem_progress(i: int, n: int, p: float) -> float:
    """Staggered per-element progress: element i starts a little after i-1, and
    every element is finished by p == 1."""
    if n <= 1:
        return _ease(p)
    step, window = 0.55 / (n - 1), 0.45
    return _ease(min(max((p - i * step) / window, 0.0), 1.0))


def _partial_path(values: list[float], p: float) -> tuple[list[float], list[float], int]:
    """The polyline truncated to fraction `p` of its total length, plus how
    many real vertices have been reached (for drawing the dots)."""
    n = len(values)
    if n < 2:
        return list(range(n)), list(values), n
    segs = [abs(values[i + 1] - values[i]) + 1.0 for i in range(n - 1)]  # +1 = x step
    total = sum(segs)
    want = _ease(p) * total
    xs, ys, reached, acc = [0.0], [values[0]], 1, 0.0
    for i, seg in enumerate(segs):
        if acc + seg <= want:
            acc += seg
            xs.append(i + 1.0)
            ys.append(values[i + 1])
            reached = i + 2
        else:
            f = (want - acc) / seg if seg else 0.0
            xs.append(i + f)
            ys.append(values[i] + (values[i + 1] - values[i]) * f)
            break
    return xs, ys, reached


def render(spec: dict, out: Path, width_px: int = 1280, progress: float = 1.0,
           lims: tuple | None = None, tight: bool = True, bbox=None):
    """Draw the chart. `progress` < 1 draws it partially (used to animate the
    chart drawing itself); `lims` pins the axes so every animation frame is
    framed identically; `tight` crops to content (off for animation frames so
    they all come out the same pixel size)."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.font_manager as fm
    import matplotlib.pyplot as plt

    kind = spec.get("type", "bar")
    labels = [str(x) for x in spec.get("labels", [])]
    values = [float(x) for x in spec.get("values", [])]
    unit = spec.get("unit", "")
    hi = spec.get("highlight")
    hi = int(hi) if isinstance(hi, (int, float)) else None
    n = len(values)

    # prefer a rounded/hand-ish font if the OS has one, else default
    fam = next((f for f in ("Comic Sans MS", "Trebuchet MS", "Verdana")
                if any(f.lower() in x.name.lower() for x in fm.fontManager.ttflist)), None)
    if fam:
        plt.rcParams["font.family"] = fam

    fig, ax = plt.subplots(figsize=(width_px / 200, width_px * 0.62 / 200), dpi=200)
    fig.patch.set_alpha(0)
    ax.set_facecolor("none")
    colors = [ACCENT if i == hi else SLATE for i in range(n)]
    vmax = max(values + [1])

    def _lab_alpha(ep: float) -> float:
        """A value label fades in as its own element finishes drawing."""
        return min(max((ep - 0.72) / 0.28, 0.0), 1.0)

    if kind == "line":
        xs, ys, reached = _partial_path(values, progress)
        ax.plot(xs, ys, color=INK, lw=6, solid_capstyle="round", zorder=3)
        if reached:
            ax.scatter(range(reached), values[:reached], s=90,
                       color=[ACCENT if i == hi else INK for i in range(reached)],
                       zorder=4)
        ax.set_xticks(range(n))
        ax.set_xticklabels(labels, fontsize=18)
        for i in {0, n - 1} | ({hi} if hi is not None else set()):
            a = 1.0 if i < reached else 0.0
            if a <= 0:
                continue
            dx = -6 if i == n - 1 else (6 if i == 0 else 0)
            ha = "right" if i == n - 1 else ("left" if i == 0 else "center")
            # Put the label on the side the line ISN'T on. A fixed "always
            # above" offset drops the text straight onto the line whenever the
            # point is a local minimum -- which is exactly what happened on a
            # V-shaped round-trip chart, printing "100" through the stroke.
            neighbours = [values[j] for j in (i - 1, i + 1) if 0 <= j < n]
            above = all(values[i] >= v for v in neighbours) if neighbours else True
            ax.annotate(_fmt(values[i], unit), (i, values[i]), textcoords="offset points",
                        xytext=(dx, 18 if above else -30), ha=ha, fontsize=18,
                        fontweight="bold", color=INK, zorder=5, alpha=a)
        ax.set_yticks([])
    elif kind == "hbar":
        eps = [_elem_progress(i, n, progress) for i in range(n)]
        ax.barh(range(n), [v * e for v, e in zip(values, eps)],
                color=colors, height=0.6, zorder=3)
        ax.set_yticks(range(n))
        ax.set_yticklabels(labels, fontsize=18)
        ax.invert_yaxis()
        ax.xaxis.set_visible(False)
        ax.spines["bottom"].set_visible(False)
        ax.set_xlim(0, vmax * 1.32)                 # room for the value labels
        for i, v in enumerate(values):
            a = _lab_alpha(eps[i])
            if a > 0:
                ax.text(v * eps[i] + vmax * 0.02, i, _fmt(v, unit), va="center",
                        fontsize=18, fontweight="bold", color=INK, alpha=a)
    else:  # bar
        eps = [_elem_progress(i, n, progress) for i in range(n)]
        ax.bar(range(n), [v * e for v, e in zip(values, eps)],
               color=colors, width=0.62, zorder=3)
        ax.set_xticks(range(n))
        # Vertical bars get one tick label each, side by side, and matplotlib
        # will happily run them into each other -- "After -50%After +50%" is
        # what shipped. Wrap anything long onto stacked lines so adjacent
        # labels can't touch.
        ax.set_xticklabels([_wrap_label(l) for l in labels], fontsize=18)
        for i, v in enumerate(values):
            a = _lab_alpha(eps[i])
            if a > 0:
                ax.text(i, v * eps[i] + vmax * 0.02, _fmt(v, unit), ha="center",
                        va="bottom", fontsize=18, fontweight="bold", color=INK, alpha=a)
        ax.set_yticks([])

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        if ax.spines[side].get_visible():
            ax.spines[side].set_color(INK)
            ax.spines[side].set_linewidth(3)
    ax.tick_params(colors=INK, length=0)
    ax.margins(x=0.17, y=0.26)
    if lims is not None:
        # Pin the axes to the FINAL chart's framing so the drawing animates
        # inside a fixed frame instead of the axes rescaling every frame
        # (which reads as the whole chart jittering).
        ax.set_xlim(lims[0])
        ax.set_ylim(lims[1])

    title_pad = 14
    if spec.get("note"):
        # red callout sits just under the title, never over the axis labels
        ax.set_title(spec.get("title", ""), fontsize=22, fontweight="bold", color=INK, pad=42)
        ax.text(0.5, 1.02, spec["note"], transform=ax.transAxes, ha="center",
                fontsize=16, fontweight="bold", color=RED)
    elif spec.get("title"):
        ax.set_title(spec["title"], fontsize=22, fontweight="bold", color=INK, pad=title_pad)

    # red hand-drawn-ish ring on the highlighted value -- lands last, once the
    # thing it is pointing at has finished drawing
    ring_a = min(max((progress - 0.75) / 0.25, 0.0), 1.0)
    if hi is not None and 0 <= hi < n and values[hi] != 0 and ring_a > 0:
        import matplotlib.patches as mpatches
        if kind == "hbar":
            xy = (values[hi] * 0.5, hi)
            w, h = max(values[hi] * 0.9, vmax * 0.25), 0.85
        elif kind == "line":
            xy = (hi, values[hi])
            w, h = 0.55, (max(values) - min(values) or vmax) * 0.30
        else:
            xy = (hi, values[hi] * 0.55)
            w, h = 0.78, values[hi] * 0.85
        ax.add_patch(mpatches.Ellipse(xy, w, h, fill=False, edgecolor=RED,
                                      lw=5, zorder=6, clip_on=False, alpha=ring_a))

    if lims is None and progress >= 1.0:
        # capture the natural framing AND the tight crop box so animation
        # frames can reuse both
        _captured_lims.clear()
        _captured_lims.extend([ax.get_xlim(), ax.get_ylim()])
        fig.canvas.draw()
        _captured_bbox.clear()
        _captured_bbox.append(
            fig.get_tightbbox(fig.canvas.get_renderer()).padded(0.4))

    out.parent.mkdir(parents=True, exist_ok=True)
    if tight:
        fig.savefig(out, transparent=True, bbox_inches="tight", pad_inches=0.4)
    elif bbox is not None:
        # Every animation frame must come out the SAME pixel size, so it can't
        # use bbox_inches="tight" (that crops to whatever is drawn *this*
        # frame). But leaving it off entirely uses matplotlib's default
        # margins, which clipped the y tick labels and the title clean off the
        # canvas ("Down 10%" rendered as "vn 10%"). Reusing the FULL chart's
        # tight box gives both: fixed size and nothing clipped.
        fig.savefig(out, transparent=True, bbox_inches=bbox)
    else:
        fig.savefig(out, transparent=True)
    plt.close(fig)
    return out


_captured_lims: list = []
_captured_bbox: list = []


def render_animation(spec: dict, out: Path, frames_dir: Path, fps: int = 30,
                     draw_s: float = 1.1, width_px: int = 1280) -> int:
    """Render the chart drawing itself: a numbered PNG sequence in
    `frames_dir` plus the finished chart at `out`.

    Static charts made every data beat a still slide -- which is both worse to
    watch and, per Meta's own guidance to creators, actively downranked as
    "low-motion / slideshow" content. The bars now grow, the line draws left to
    right, value labels fade in as their element lands, and the highlight ring
    arrives last.

    Frames are rendered at a FIXED figure size (no tight bbox, which would give
    every frame a different size) and then all cropped to the final frame's
    alpha bounding box, so the sequence is uniform AND tight to content -- and
    identical in size to `out`, which the layout engine measures.
    """
    from PIL import Image

    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("*.png"):
        old.unlink()

    # pass 1: full chart, natural framing -> capture axis limits AND crop box
    render(spec, out, width_px=width_px, progress=1.0, tight=True)
    lims = tuple(_captured_lims) if len(_captured_lims) == 2 else None
    bbox = _captured_bbox[0] if _captured_bbox else None

    n_frames = max(2, int(round(fps * draw_s)))
    raw = []
    for i in range(n_frames):
        p = (i + 1) / n_frames
        fp = frames_dir / f"{i + 1:04d}.png"
        render(spec, fp, width_px=width_px, progress=p, lims=lims,
               tight=False, bbox=bbox)
        raw.append(fp)

    # crop every frame to the LAST frame's content box (same box => no drift)
    with Image.open(raw[-1]) as last:
        bbox = last.convert("RGBA").getchannel("A").getbbox()
    if bbox:
        pad = 8
        x0, y0, x1, y1 = bbox
        with Image.open(raw[-1]) as probe:
            W, H = probe.size
        box = (max(0, x0 - pad), max(0, y0 - pad),
               min(W, x1 + pad), min(H, y1 + pad))
        for fp in raw:
            with Image.open(fp) as im:
                im.convert("RGBA").crop(box).save(fp)
        with Image.open(raw[-1]) as im:
            im.save(out)          # keep `out` byte-identical to the last frame
    return len(raw)
