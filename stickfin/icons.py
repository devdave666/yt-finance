"""The fixed prop-icon library.

Icons are generated once by tools/build_icons.py and committed to
assets/icons/*.png. The pipeline composites these directly instead of
generating a prop per video -- that was the weak spot (inconsistent, too
realistic). The generator is told to pick prop names from this vocabulary;
anything not in it falls back to per-video generation in assets.py.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

ICONS_DIR = Path("assets/icons")


@lru_cache(maxsize=1)
def names() -> tuple[str, ...]:
    if not ICONS_DIR.exists():
        return ()
    return tuple(sorted(p.stem for p in ICONS_DIR.glob("*.png")))


def path(name: str) -> Path | None:
    p = ICONS_DIR / f"{name}.png"
    return p if p.exists() else None
