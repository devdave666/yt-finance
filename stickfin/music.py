"""Real background music -- a curated local library, section-aware for long-form.

`assets/music/<mood>/*.{mp3,wav,m4a,ogg,flac}` -- Dev curates this once (see
`assets/music/README.md`: YouTube Audio Library, "no attribution required"
tracks only). Nothing here ever calls out to the network; if the library is
empty this whole module is a no-op and the pipeline falls back to
`sfx.build_bed`'s synthesised drone -- so a bare checkout still renders.

Long-form videos change track at each section instead of looping one track
for 5 minutes straight -- a section boundary is an `emphasis: true` beat, the
same markers `sfx.py` uses for the riser accent, so "the video moved to a new
part" and "the music moved to a new part" always land together. Sections
crossfade (ffmpeg `acrossfade`) into each other, never a hard music cut.
A Short is one section, full stop -- 25-30s is too brief to change tracks.

Mood is inferred from the script, not hand-tagged: the last section is
"resolve", any section containing a `tone: negative` beat is "tense", a skit
is "playful" throughout, everything else is "neutral". A missing mood folder
falls back to "neutral", then to whatever mood has any tracks at all.

LEVELS ARE UNTESTED BY EAR -- no tracks are committed yet. MUSIC_BED_LUFS
(config.py) is the one knob to turn once Dev has actually listened to a render.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from . import config
from .ffmpeg_util import run_ffmpeg

MUSIC_DIR = Path("assets/music")
HISTORY = Path("state/music_history.json")
_EXTS = {".mp3", ".wav", ".m4a", ".ogg", ".flac"}


def library() -> dict[str, list[Path]]:
    if not MUSIC_DIR.exists():
        return {}
    lib: dict[str, list[Path]] = {}
    for d in sorted(MUSIC_DIR.iterdir()):
        if d.is_dir():
            files = sorted(p for p in d.iterdir() if p.suffix.lower() in _EXTS)
            if files:
                lib[d.name] = files
    return lib


def _history() -> list[str]:
    if HISTORY.exists():
        return json.loads(HISTORY.read_text())
    return []


def _remember(paths: list[Path]) -> None:
    hist = _history() + [str(p) for p in paths]
    HISTORY.parent.mkdir(parents=True, exist_ok=True)
    HISTORY.write_text(json.dumps(hist[-40:], indent=2))


def _pick(mood: str, lib: dict[str, list[Path]], avoid: set[str]) -> Path | None:
    pool = lib.get(mood) or lib.get("neutral") or next(iter(lib.values()), None)
    if not pool:
        return None
    eligible = [p for p in pool if str(p) not in avoid] or pool
    return random.choice(eligible)


def _sections(script, narration: dict) -> list[dict]:
    """[{start_s, dur_s, mood}], oldest first.

    One section per `emphasis: true` beat for long-form; a single section for
    everything else (Shorts, and long-form scripts with no emphasis markers).
    """
    dur_by_id = {b["id"]: b["duration_s"] for b in narration["beats"]}
    is_wide = script.fmt == "wide"
    sections: list[dict] = []
    t = 0.0
    for beat in script.beats:
        d = dur_by_id.get(beat.id, 0.0)
        if not sections or (is_wide and beat.emphasis):
            sections.append({"start_s": t, "dur_s": 0.0, "tense": False})
        sections[-1]["dur_s"] += d
        if beat.tone == "negative":
            sections[-1]["tense"] = True
        t += d

    is_skit = script.caption_style == "title"
    for i, sec in enumerate(sections):
        if not is_wide:
            sec["mood"] = "playful" if is_skit else "neutral"
        elif i == len(sections) - 1:
            sec["mood"] = "resolve"
        elif sec["tense"]:
            sec["mood"] = "tense"
        else:
            sec["mood"] = "neutral"
    return sections


def build_bed(script, narration: dict, out_wav: Path) -> Path | None:
    """The assembled, crossfaded, loudness-normalised music bed, or None if
    the local library is empty (caller should fall back to sfx.build_bed)."""
    lib = library()
    if not lib:
        return None

    sections = _sections(script, narration)
    cf = config.MUSIC_CROSSFADE_S
    picks: list[Path] = []
    clips: list[Path] = []
    tmp_dir = out_wav.parent / "_music_tmp"
    tmp_dir.mkdir(exist_ok=True)
    avoid = set(_history())

    for i, sec in enumerate(sections):
        track = _pick(sec["mood"], lib, avoid | {str(p) for p in picks})
        if track is None:
            return None
        picks.append(track)
        need = sec["dur_s"] + (cf if i < len(sections) - 1 else 0.0)
        clip = tmp_dir / f"sec{i:02d}.wav"
        run_ffmpeg(
            ["-y", "-stream_loop", "-1", "-i", str(track), "-t", f"{max(need, 1.0):.3f}",
             "-af", f"loudnorm=I={config.MUSIC_BED_LUFS}:TP=-3:LRA=11,"
                    "aformat=channel_layouts=stereo", "-ar", "48000", clip],
            f"music section {i} ({sec['mood']}) <- {track.name}")
        clips.append(clip)

    if len(clips) == 1:
        run_ffmpeg(["-y", "-i", str(clips[0]), "-ar", "48000", out_wav],
                   "music bed (single section)")
    else:
        inputs = []
        for c in clips:
            inputs += ["-i", str(c)]
        chain, parts = "[0:a]", []
        for i in range(1, len(clips)):
            out_lbl = f"[x{i}]" if i < len(clips) - 1 else "[out]"
            parts.append(f"{chain}[{i}:a]acrossfade=d={cf}:c1=tri:c2=tri{out_lbl}")
            chain = out_lbl
        run_ffmpeg(["-y", *inputs, "-filter_complex", ";".join(parts),
                    "-map", "[out]", "-ar", "48000", out_wav],
                   f"music crossfade ({len(clips)} sections)")

    for c in clips:
        c.unlink(missing_ok=True)
    try:
        tmp_dir.rmdir()
    except OSError:
        pass

    _remember(picks)
    labels = ", ".join(f"{s['mood']}:{p.name}" for s, p in zip(sections, picks))
    print(f"  music: {labels}")
    return out_wav


def resolve_bed(script, narration: dict, build_dir: Path):
    """(bed_path, music_db) for assemble.mux() -- the curated real library
    first, the synthesised long-form drone (sfx.build_bed) as a fallback so a
    bare checkout with no music/ files still renders, else no bed at all."""
    if config.USE_REAL_MUSIC:
        bed = build_bed(script, narration, build_dir / "music_bed.wav")
        if bed:
            return bed, 0.0  # already loudness-normalised, no extra trim
    if getattr(config, "AMBIENT_BED", True) and script.fmt == "wide":
        from . import sfx as sfx_mod
        return sfx_mod.build_bed(narration["total_s"], build_dir / "bed.wav"), None
    return None, None
