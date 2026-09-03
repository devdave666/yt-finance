"""Load + validate a video script (YAML).

A script is a cast + a set of scenes + an ordered list of beats. Each beat is
one spoken line (skit) or one narration line (explainer), plus which characters
are on screen and in what pose/expression, plus any props, photoreal cutouts,
or a live-action clip.

See scripts/*.yaml for worked examples of both modes.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import config

_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,60}$")
_CAPTION_STYLES = {"explainer", "skit", "title", "subtitle", "none"}
_ANCHORS = {"left", "center", "right"}


@dataclass
class Character:
    name: str
    voice: str
    look: str
    anchor: str = "center"
    scale: float = 1.0


@dataclass
class Scene:
    name: str
    bg: str | None = None       # generation prompt; None => flat colour / white
    color: str | None = None    # flat background colour (hex); default white


@dataclass
class Cutout:
    src: str                    # local path or http(s) URL
    at: str = "center"
    scale: float = 0.5          # fraction of canvas height
    behind: bool = False        # draw behind characters


@dataclass
class Beat:
    id: str
    scene: str | None
    who: str | None             # speaking character; None => narrator
    say: str
    cast: dict[str, str] = field(default_factory=dict)   # name -> "pose, expression"
    props: list[str] = field(default_factory=list)        # generated prop names
    cutouts: list[Cutout] = field(default_factory=list)
    chart: dict | None = None    # {type, title, labels[], values[], unit, highlight, note}
    headline: str | None = None  # big hook text on beat 1 (a number / short punch)
    live: dict | None = None     # {"src": ..., "trim": "0:00-0:03"}
    emphasis: bool = False
    tone: str = ""               # "negative" => red edge-vignette washed over the frame

    @property
    def is_live(self) -> bool:
        return self.live is not None


@dataclass
class Script:
    title: str
    slug: str
    fmt: str
    caption_style: str
    title_card: str | None
    music: str | None
    narrator_voice: str
    cast: dict[str, Character]
    scenes: dict[str, Scene]
    beats: list[Beat]

    @property
    def build_dir(self) -> Path:
        return Path("build") / self.slug

    @property
    def out_path(self) -> Path:
        return self.build_dir / f"{self.slug}.mp4"

    def voice_for(self, beat: Beat) -> str:
        if beat.who and beat.who in self.cast:
            return self.cast[beat.who].voice
        return self.narrator_voice


def _parse_chart(raw, bid: str) -> dict | None:
    if not raw:
        return None
    labels = [str(x) for x in (raw.get("labels") or [])]
    try:
        values = [float(str(x).replace(",", "")) for x in (raw.get("values") or [])]
    except (TypeError, ValueError):
        raise ValueError(f"beat {bid!r}: chart values must be numbers")
    if len(labels) != len(values) or len(values) < 2:
        raise ValueError(f"beat {bid!r}: chart needs >=2 matching labels and values")
    hi = raw.get("highlight")
    return {
        "type": raw.get("type", "bar") if raw.get("type") in ("bar", "hbar", "line") else "bar",
        "title": str(raw.get("title", "")).strip(),
        "labels": labels,
        "values": values,
        "unit": str(raw.get("unit", "")).strip(),
        "highlight": int(hi) if isinstance(hi, (int, float)) and 0 <= int(hi) < len(values) else None,
        "note": str(raw.get("note", "")).strip(),
    }


def _parse_cutout(raw) -> Cutout:
    if isinstance(raw, str):
        return Cutout(src=raw)
    return Cutout(src=raw["src"], at=raw.get("at", "center"),
                  scale=float(raw.get("scale", 0.5)),
                  behind=bool(raw.get("behind", False)))


def load_script(path) -> Script:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError("script must be a YAML mapping")

    for k in ("title", "slug", "beats"):
        if not data.get(k):
            raise ValueError(f"script missing required key: {k}")

    slug = str(data["slug"]).strip()
    if not _SLUG_RE.match(slug):
        raise ValueError(f"slug {slug!r} must be 2-61 chars of [a-z0-9-]")

    fmt = str(data.get("format", config.DEFAULT_FORMAT)).strip()
    if fmt not in config.FORMATS:
        raise ValueError(f"format must be one of {sorted(config.FORMATS)}")

    caption_style = str(data.get("caption_style", "skit")).strip()
    if caption_style not in _CAPTION_STYLES:
        raise ValueError(f"caption_style must be one of {sorted(_CAPTION_STYLES)}")

    narrator_voice = str(
        (data.get("narrator") or {}).get("voice") or data.get("voice")
        or config.DEFAULT_VOICE).strip()

    cast: dict[str, Character] = {}
    for name, c in (data.get("cast") or {}).items():
        c = c or {}
        anchor = str(c.get("anchor", "center"))
        if anchor not in _ANCHORS:
            raise ValueError(f"cast {name!r}: anchor must be left/center/right")
        cast[name] = Character(
            name=name,
            voice=str(c.get("voice") or narrator_voice),
            look=str(c.get("look") or "").strip(),
            anchor=anchor,
            scale=float(c.get("scale", 1.0)),
        )

    scenes: dict[str, Scene] = {}
    for name, s in (data.get("scenes") or {}).items():
        s = s or {}
        scenes[name] = Scene(name=name, bg=(s.get("bg") or None),
                             color=(s.get("color") or None))

    beats: list[Beat] = []
    seen: set[str] = set()
    for i, raw in enumerate(data["beats"]):
        if not isinstance(raw, dict):
            raise ValueError(f"beat #{i} must be a mapping")
        bid = str(raw.get("id") or f"b{i:03d}").strip()
        if bid in seen:
            raise ValueError(f"duplicate beat id {bid!r}")
        seen.add(bid)

        live = raw.get("live")
        if live:
            live = {"src": str(live["src"]), "trim": str(live.get("trim", ""))}
            beats.append(Beat(id=bid, scene=None, who=None, say="", live=live))
            continue

        say = str(raw.get("say") or "").strip()
        say = re.sub(r"(?<!\w)[*_]+(?=\w)|(?<=\w)[*_]+(?!\w)", "", say)  # strip md emphasis
        if not say:
            raise ValueError(f"beat {bid!r} needs 'say' (or 'live')")

        scene = raw.get("scene")
        if scene and scene not in scenes:
            raise ValueError(f"beat {bid!r}: unknown scene {scene!r}")

        who = raw.get("who")
        if who and who not in cast:
            raise ValueError(f"beat {bid!r}: unknown speaker {who!r}")

        cast_state = {str(k): str(v) for k, v in (raw.get("cast") or {}).items()}
        for cname in cast_state:
            if cname not in cast:
                raise ValueError(f"beat {bid!r}: unknown character {cname!r} in cast")

        beats.append(Beat(
            id=bid,
            scene=scene,
            who=who,
            say=say,
            cast=cast_state,
            props=[str(p) for p in (raw.get("props") or [])],
            cutouts=[_parse_cutout(c) for c in (raw.get("cutouts") or [])],
            chart=_parse_chart(raw.get("chart"), bid),
            headline=(str(raw["headline"]).strip()[:60] if raw.get("headline") else None),
            emphasis=bool(raw.get("emphasis")),
            tone=("negative" if str(raw.get("tone") or "").strip().lower() == "negative" else ""),
        ))

    if not beats:
        raise ValueError("script has no beats")

    return Script(
        title=str(data["title"]).strip(),
        slug=slug,
        fmt=fmt,
        caption_style=caption_style,
        title_card=(str(data["title_card"]).strip() if data.get("title_card") else None),
        music=(str(data["music"]).strip() if data.get("music") else None),
        narrator_voice=narrator_voice,
        cast=cast,
        scenes=scenes,
        beats=beats,
    )
