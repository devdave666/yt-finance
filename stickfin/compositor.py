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


def _resolve(layer: dict, adir: Path) -> Path | None:
    sub = {"character": "char", "prop": "prop", "cutout": "cutout",
           "chart": "chart", "headline": "headline"}.get(layer["type"])
    if sub is None:
        return None
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

    # resolve every layer, then let layout.solve place them all so nothing
    # collides or leaves the frame
    resolved = []
    for layer in shot["layers"]:
        ap = _resolve(layer, adir)
        if ap is None:
            print(f"    ! missing asset {layer['type']}/{layer['asset']} -- skipped")
            continue
        resolved.append((layer, ap, _img_size(ap)))
    placements = layout.solve([{"type": l["type"], "wh": wh} for l, _, wh in resolved], fmt)

    # a character should face what it's talking about. Poses are generated
    # gesturing to the viewer's right, so mirror any character that ends up to
    # the RIGHT of the shot's other content (chart / prop / headline).
    others = [b for (l, _, _), b in zip(resolved, placements) if l["type"] != "character"]
    focus_cx = (sum(bx + bw / 2 for bx, _, bw, _ in others) / len(others)
                if others else None)

    pop = max(config.POP_IN_S, 0.001)
    dur_s = nf / fps
    idx, last, char_seen = 1, "b0", 0
    for (layer, ap, _wh), (x, y, w, h) in zip(resolved, placements):
        # A chart that has a rendered frame sequence beside it draws itself on
        # instead of cutting in finished. tpad clones the final frame for the
        # rest of the shot, so the chart holds once it has finished drawing.
        seq = ap.parent / ap.stem
        animated = layer["type"] == "chart" and seq.is_dir() and any(seq.glob("*.png"))
        if animated:
            inputs += ["-framerate", str(fps), "-i", str(seq / "%04d.png")]
        else:
            inputs += ["-loop", "1", "-i", str(ap)]
        cur = f"c{idx}"
        flip = (layer["type"] == "character" and focus_cx is not None
                and focus_cx < (x + w / 2) - 0.03 * cw)
        hold = (f",tpad=stop_mode=clone:stop_duration={dur_s + 1:.3f}"
                if animated else "")
        # every layer fades + settles up into place on the cut (the "pop")
        chains.append(f"[{idx}:v]scale={w}:{h}{',hflip' if flip else ''},format=rgba"
                      f"{hold},fade=t=in:st=0:d={pop}:alpha=1[s{idx}]")

        settle = f"-18*(1-min(1,t/{pop}))"
        if layer["type"] == "character" and config.IDLE_BOB_PX > 0:
            phase = char_seen * 3.14159
            char_seen += 1
            amp, hz = config.IDLE_BOB_PX, config.IDLE_BOB_HZ
            ye = f"{y}{settle}+{amp}*sin(2*PI*{hz}*t+{phase:.3f})"
        else:
            ye = f"{y}{settle}"
        chains.append(f"[{last}][s{idx}]overlay={x}:'{ye}':format=auto[{cur}]")
        last, idx = cur, idx + 1

    # tone:negative -> wash a red edge-vignette over the finished frame
    tint = adir / "fx" / "red_vignette.png"
    if shot.get("tone") == "negative" and tint.exists():
        inputs += ["-loop", "1", "-i", str(tint)]
        chains.append(f"[{idx}:v]scale={cw}:{ch},setsar=1,format=rgba,"
                      f"fade=t=in:st=0:d={pop}:alpha=1[rt]")
        chains.append(f"[{last}][rt]overlay=0:0:format=auto[rtd]")
        last, idx = "rtd", idx + 1

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
