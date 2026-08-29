"""Thin ffmpeg/ffprobe wrappers. Both binaries must be on PATH."""
from __future__ import annotations

import subprocess
from pathlib import Path


class FFmpegError(RuntimeError):
    pass


def run_ffmpeg(args, desc: str) -> None:
    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error", *(str(a) for a in args)]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise FFmpegError(f"ffmpeg failed ({desc}):\n{result.stderr[-2500:]}")


def probe_duration(path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise FFmpegError(f"ffprobe failed for {path}:\n{result.stderr[-800:]}")
    return float(result.stdout.strip())


def concat_reencode(clip_paths, out_path, fps: int, extra_v=None) -> None:
    """Concatenate clips by RE-ENCODING (never -c copy).

    core-decor-automation hit a real truncation bug trusting stream-copy concat
    once; re-encoding normalizes timebases/params and is safe at any clip count.
    """
    listfile = Path(out_path).with_suffix(".concat.txt")
    listfile.write_text(
        "".join(f"file '{Path(p).resolve().as_posix()}'\n" for p in clip_paths),
        encoding="utf-8",
    )
    run_ffmpeg(
        ["-f", "concat", "-safe", "0", "-i", listfile,
         *(extra_v or []),
         "-c:v", "libx264", "-preset", "medium", "-crf", "18",
         "-pix_fmt", "yuv420p", "-r", fps, out_path],
        f"concat {len(clip_paths)} clips -> {Path(out_path).name}",
    )
    listfile.unlink(missing_ok=True)
