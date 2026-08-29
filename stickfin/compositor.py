"""Render each shot as a flat composite (static camera, hard cuts).

Composite shot = background image + scaled PNG layers (characters, props,
cutouts) overlaid at positions computed by layout.py from the real asset
dimensions. Live shot = the source clip scaled to cover the canvas and
center-cropped. No Ken Burns, no tweening -- movement between shots is the
hard cut, exactly like the reference reels.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import config, layout
from .ffmpeg_util import concat_reencode, run_ffmpeg


def _img_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def _box_for(layer: dict, asset_path: Path, fmt: str) -> tuple[int, int, int, int]:
    wh = _img_size(asset_path)
    if layer["type"] == "character":
        box = layout.character_box(layer.get("anchor", "center"),
                                   float(layer.get("scale", 1.0)), wh, fmt)
    else:
        box = layout.object_box(layer.get("at", "center"),
                                float(layer.get("scale", 0.4)), wh, fmt)
    return layout.clamp(box, fmt)


def _resolve(layer: dict, adir: Path) -> Path | None:
    sub = {"character": "char", "prop": "prop", "cutout": "cutout"}[layer["type"]]
    p = adir / sub / f"{layer['asset']}.png"
    return p if p.exists() else None


def _composite_clip(shot: dict, adir: Path, fmt: str, out: Path) -> None:
    cw, ch = config.canvas(fmt)
    fps, nf = config.FPS, int(shot["frames"])

    inputs: list[str] = []
    if shot.get("scene"):
        bg = adir / "bg" / f"{shot['scene']}.png"
        inputs += ["-loop", "1", "-i", str(bg)]
        chains = [f"[0:v]scale={cw}:{ch},setsar=1,fps={fps}[b0]"]
    else:
        inputs += ["-f", "lavfi", "-i", f"color=c=white:s={cw}x{ch}:r={fps}"]
        chains = ["[0:v]setsar=1[b0]"]

    idx, last = 1, "b0"
    for layer in shot["layers"]:
        ap = _resolve(layer, adir)
        if ap is None:
            print(f"    ! missing asset {layer['type']}/{layer['asset']} -- skipped")
            continue
        x, y, w, h = _box_for(layer, ap, fmt)
        inputs += ["-loop", "1", "-i", str(ap)]
        cur = f"c{idx}"
        chains.append(f"[{idx}:v]scale={w}:{h}[s{idx}]")
        chains.append(f"[{last}][s{idx}]overlay={x}:{y}:format=auto[{cur}]")
        last, idx = cur, idx + 1

    run_ffmpeg(
        ["-y", *inputs, "-filter_complex", ";".join(chains), "-map", f"[{last}]",
         "-frames:v", nf, "-r", fps, "-c:v", "libx264", "-preset", "medium",
         "-crf", "18", "-pix_fmt", "yuv420p", out],
        f"composite {shot['beat_id']}#{shot['index']} ({len(shot['layers'])} layers, {nf}f)")


def _live_clip(shot: dict, fmt: str, out: Path) -> None:
    cw, ch = config.canvas(fmt)
    fps, nf = config.FPS, int(shot["frames"])
    trim = shot["live"].get("trim", "")
    lo = 0.0
    if trim:
        head = trim.split("-")[0].strip()
        lo = int(head.split(":")[0]) * 60 + float(head.split(":")[1]) if ":" in head else float(head or 0)
    run_ffmpeg(
        ["-ss", str(lo), "-i", shot["live"]["src"], "-an",
         "-vf", f"scale={cw}:{ch}:force_original_aspect_ratio=increase,"
                f"crop={cw}:{ch},setsar=1,fps={fps},"
                f"tpad=stop_mode=clone:stop_duration=2",
         "-frames:v", nf, "-r", fps, "-c:v", "libx264", "-preset", "medium",
         "-crf", "18", "-pix_fmt", "yuv420p", out],
        f"live {shot['beat_id']} ({nf}f)")


def render_shots(script, timeline: dict) -> Path:
    adir = script.build_dir / "assets"
    clips_dir = script.build_dir / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    paths = []
    for i, shot in enumerate(timeline["shots"]):
        clip = clips_dir / f"{i:04d}_{shot['beat_id']}_{shot['index']}.mp4"
        if shot["kind"] == "live":
            _live_clip(shot, script.fmt, clip)
        else:
            _composite_clip(shot, adir, script.fmt, clip)
        paths.append(clip)

    silent = script.build_dir / "_silent.mp4"
    concat_reencode(paths, silent, config.FPS)
    return silent
