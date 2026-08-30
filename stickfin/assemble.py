"""Final mux: composite video + voiceover + SFX (+ music bed) + burned captions."""
from __future__ import annotations

from pathlib import Path

from .ffmpeg_util import probe_duration, run_ffmpeg


def _sub_arg(path: Path) -> str:
    p = path.resolve().as_posix().replace(":", "\\:")
    return f"subtitles='{p}'"


def mux(script, silent: Path, voiceover: Path, captions: Path | None,
        music: str | None, sfx: Path | None = None) -> Path:
    out = script.out_path
    args = ["-i", str(silent), "-i", str(voiceover)]
    filters: list[str] = []
    vmap = "0:v"

    # ---- audio graph: VO, then SFX on top, then duck a music bed under both ----
    idx = 2
    voice_bus = "[1:a]"
    amap = "1:a"                     # raw-stream form for -map, unless a filter runs
    if sfx:
        args += ["-i", str(sfx)]
        filters.append(f"{voice_bus}[{idx}:a]amix=inputs=2:normalize=0:"
                       f"duration=first[vs]")
        voice_bus, amap = "[vs]", "[vs]"
        idx += 1

    if music:
        args += ["-stream_loop", "-1", "-i", str(music)]
        filters.append(f"[{idx}:a]volume=-22dB,afade=t=in:st=0:d=1.2[bed]")
        filters.append(f"{voice_bus}[bed]sidechaincompress=threshold=0.02:"
                       "ratio=10:attack=5:release=300[duck]")
        filters.append(f"{voice_bus}[duck]amix=inputs=2:duration=first:"
                       "dropout_transition=0[a]")
        amap = "[a]"

    if captions:
        filters.append(f"[0:v]{_sub_arg(captions)}[v]")
        vmap = "[v]"

    if filters:
        args += ["-filter_complex", ";".join(filters)]

    args += ["-map", vmap, "-map", amap]
    args += (["-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p"]
             if captions else ["-c:v", "copy"])
    args += ["-c:a", "aac", "-b:a", "192k", "-movflags", "+faststart",
             "-shortest", str(out)]

    run_ffmpeg(args, "final mux")
    print(f"  video {probe_duration(out):.2f}s / audio {probe_duration(voiceover):.2f}s")
    return out
