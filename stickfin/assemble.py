"""Final mux: composite video + voiceover + SFX (+ music bed) + burned captions."""
from __future__ import annotations

from pathlib import Path

from . import config
from .ffmpeg_util import probe_duration, run_ffmpeg


def _sub_arg(path: Path) -> str:
    p = path.resolve().as_posix().replace(":", "\\:")
    return f"subtitles='{p}'"


def mux(script, silent: Path, voiceover: Path, captions: Path | None,
        music: str | None, sfx: Path | None = None,
        music_db: float | None = None) -> Path:
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
        # bed (build_bed) is already video-length; no -stream_loop. Mixed FLAT,
        # not sidechain-ducked: a near-continuous narration never lets a ducked
        # bed back up, so it just vanishes (measured: 0.4 dB). A steady low bed
        # -- lo-fi/study-channel style -- reads as atmosphere under talking.
        # A gentle compressor keeps its own swells from poking through.
        # music_db lets the caller override the trim -- the synthesised drone
        # (sfx.build_bed) is calibrated for config.AMBIENT_BED_DB, but a real
        # curated track (music.build_bed) is already loudness-normalised to
        # config.MUSIC_BED_LUFS and wants ~0 extra trim, not the drone's -8.
        bed_db = float(config.AMBIENT_BED_DB if music_db is None else music_db)
        args += ["-i", str(music)]
        filters.append(
            f"[{idx}:a]volume={bed_db}dB,acompressor=threshold=-18dB:ratio=3:"
            f"attack=50:release=400,afade=t=in:st=0:d=3[bed]")
        filters.append(f"{voice_bus}[bed]amix=inputs=2:normalize=0:duration=first:"
                       f"dropout_transition=0[a]")
        amap = "[a]"
        idx += 1

    # master limiter: the SFX bus (and a music bed) stack on top of an already
    # loudness-normalised voice, so the sum can brush 0 dBFS and clip. Cap it
    # ~1 dB below full scale. level=disabled -> limit only, no make-up gain.
    a_in = amap if amap.startswith("[") else f"[{amap}]"
    filters.append(f"{a_in}alimiter=level=disabled:limit=0.891[master]")
    amap = "[master]"

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
