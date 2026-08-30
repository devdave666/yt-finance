"""Narration timings -> a shot list where no drawing holds longer than MAX_HOLD_S.

Pure planning: no API calls, no file-size lookups (the compositor resolves
pixel positions from the real asset dimensions at render time). Emits
timeline.json.

A beat's screen time is its VO / live-clip length. If that exceeds MAX_HOLD_S
the beat is split into equal holds of the same composite -- skit lines are
short enough that this rarely fires; explainer holds lean on phrase-by-phrase
captions for motion.
"""
from __future__ import annotations

import json
import math

from . import config
from .assets import slug


# prop slots beside the figure, clear of its head
_PROP_SLOTS = ["right-low", "right-mid", "right-top", "center-bottom"]


def _layers_for(script, beat) -> list[dict]:
    layers: list[dict] = []
    front_cutouts = [c for c in beat.cutouts if not c.behind]
    has_objects = bool(beat.props or front_cutouts)

    for co in beat.cutouts:
        if co.behind:
            layers.append({"type": "cutout", "asset": _cut_key(co.src),
                           "at": co.at, "scale": co.scale})

    for cname, state in beat.cast.items():
        ch = script.cast[cname]
        # a centred solo speaker slides aside when objects share the frame
        anchor = ch.anchor
        if anchor == "center" and has_objects and len(beat.cast) == 1:
            anchor = config.CHAR_ANCHOR_WITH_PROPS
        layers.append({"type": "character",
                       "asset": f"{cname}__{slug(state)}",
                       "anchor": anchor, "scale": ch.scale})

    for i, p in enumerate(beat.props):
        layers.append({"type": "prop", "asset": slug(p),
                       "at": _PROP_SLOTS[i % len(_PROP_SLOTS)],
                       "scale": config.PROP_SCALE})

    for co in front_cutouts:
        layers.append({"type": "cutout", "asset": _cut_key(co.src),
                       "at": co.at, "scale": co.scale})
    return layers


def _cut_key(src: str) -> str:
    import hashlib
    return hashlib.sha1(src.encode()).hexdigest()[:12]


def plan(script, narration: dict) -> dict:
    fps = config.FPS
    dur_by_id = {b["id"]: b["duration_s"] for b in narration["beats"]}
    beat_by_id = {b.id: b for b in script.beats}

    shots: list[dict] = []
    frame_cursor = 0        # exact running position, in frames
    seconds_cursor = 0.0    # exact running position, in seconds (for beat edges)

    for entry in narration["beats"]:
        beat = beat_by_id[entry["id"]]
        d = dur_by_id[beat.id]
        seconds_cursor += d
        beat_end_frame = round(seconds_cursor * fps)
        beat_frames = max(1, beat_end_frame - frame_cursor)

        if beat.is_live:
            n_holds = 1
        else:
            n_holds = max(1, math.ceil((d - 1e-3) / config.MAX_HOLD_S))
            while n_holds > 1 and (beat_frames / n_holds) / fps < config.MIN_HOLD_S:
                n_holds -= 1

        layers = [] if beat.is_live else _layers_for(script, beat)
        for k in range(n_holds):
            # distribute beat_frames across holds with no rounding loss
            f0 = frame_cursor + round(beat_frames * k / n_holds)
            f1 = frame_cursor + round(beat_frames * (k + 1) / n_holds)
            nf = max(1, f1 - f0)
            shot = {
                "beat_id": beat.id, "index": k, "n": n_holds,
                "start_frame": f0, "frames": nf,
                "start_s": round(f0 / fps, 3), "dur_s": round(nf / fps, 3),
                "kind": "live" if beat.is_live else "composite",
                "scene": None if beat.is_live else beat.scene,
                "layers": layers,
                "emphasis": beat.emphasis if not beat.is_live else False,
            }
            if beat.is_live:
                shot["live"] = beat.live
            shots.append(shot)
        frame_cursor = beat_end_frame

    timeline = {
        "slug": script.slug,
        "fmt": script.fmt,
        "fps": fps,
        "caption_style": script.caption_style,
        "total_frames": frame_cursor,
        "total_s": round(frame_cursor / fps, 3),
        "shot_count": len(shots),
        "shots": shots,
    }
    script.build_dir.mkdir(parents=True, exist_ok=True)
    (script.build_dir / "timeline.json").write_text(json.dumps(timeline, indent=2))
    return timeline
