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


def render(spec: dict, out: Path, width_px: int = 1280) -> Path:
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

    if kind == "line":
        ax.plot(range(n), values, color=INK, lw=6, solid_capstyle="round", zorder=3)
        ax.scatter(range(n), values, s=90,
                   color=[ACCENT if i == hi else INK for i in range(n)], zorder=4)
        ax.set_xticks(range(n))
        ax.set_xticklabels(labels, fontsize=18)
        for i in {0, n - 1} | ({hi} if hi is not None else set()):
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
                        fontweight="bold", color=INK, zorder=5)
        ax.set_yticks([])
    elif kind == "hbar":
        ax.barh(range(n), values, color=colors, height=0.6, zorder=3)
        ax.set_yticks(range(n))
        ax.set_yticklabels(labels, fontsize=18)
        ax.invert_yaxis()
        ax.xaxis.set_visible(False)
        ax.spines["bottom"].set_visible(False)
        ax.set_xlim(0, vmax * 1.32)                 # room for the value labels
        for i, v in enumerate(values):
            ax.text(v + vmax * 0.02, i, _fmt(v, unit), va="center", fontsize=18,
                    fontweight="bold", color=INK)
    else:  # bar
        ax.bar(range(n), values, color=colors, width=0.62, zorder=3)
        ax.set_xticks(range(n))
        # Vertical bars get one tick label each, side by side, and matplotlib
        # will happily run them into each other -- "After -50%After +50%" is
        # what shipped. Wrap anything long onto stacked lines so adjacent
        # labels can't touch.
        ax.set_xticklabels([_wrap_label(l) for l in labels], fontsize=18)
        for i, v in enumerate(values):
            ax.text(i, v + vmax * 0.02, _fmt(v, unit), ha="center", va="bottom",
                    fontsize=18, fontweight="bold", color=INK)
        ax.set_yticks([])

    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        if ax.spines[side].get_visible():
            ax.spines[side].set_color(INK)
            ax.spines[side].set_linewidth(3)
    ax.tick_params(colors=INK, length=0)
    ax.margins(x=0.17, y=0.26)

    title_pad = 14
    if spec.get("note"):
        # red callout sits just under the title, never over the axis labels
        ax.set_title(spec.get("title", ""), fontsize=22, fontweight="bold", color=INK, pad=42)
        ax.text(0.5, 1.02, spec["note"], transform=ax.transAxes, ha="center",
                fontsize=16, fontweight="bold", color=RED)
    elif spec.get("title"):
        ax.set_title(spec["title"], fontsize=22, fontweight="bold", color=INK, pad=title_pad)

    # red hand-drawn-ish ring on the highlighted value
    if hi is not None and 0 <= hi < n and values[hi] != 0:
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
                                      lw=5, zorder=6, clip_on=False))

    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, transparent=True, bbox_inches="tight", pad_inches=0.4)
    plt.close(fig)
    return out
