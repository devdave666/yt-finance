"""Procedural sound effects -- no audio assets, no licensing.

Four tiny sounds are synthesised once with ffmpeg lavfi and cached in
assets/sfx/. build_track() lays them along the timeline:

  whoosh  once, on the opening hook
  tick    a soft click on every hard cut (very quiet -- it's just pace)
  pop     when a new prop/icon first appears
  chime   a soft resolve at the start of the final beat

The result is one stereo wav the length of the video, mixed under the VO in
assemble.mux().
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from . import config
from .ffmpeg_util import run_ffmpeg

SFX_DIR = Path("assets/sfx")
SR = config.TTS_SAMPLE_RATE

# name -> lavfi source + filter chain producing a short mono blip
_RECIPES = {
    "tick": (
        f"anoisesrc=d=0.045:c=pink:r={SR}:a=0.9",
        "highpass=f=2000,lowpass=f=9000,afade=t=out:st=0.006:d=0.038",
    ),
    "pop": (
        f"sine=frequency=600:duration=0.11:sample_rate={SR}",
        "afade=t=in:d=0.004,afade=t=out:st=0.02:d=0.09",
    ),
    "whoosh": (
        f"anoisesrc=d=0.28:c=pink:r={SR}:a=0.9",
        "highpass=f=500,lowpass=f=5400,afade=t=in:d=0.08,afade=t=out:st=0.12:d=0.16",
    ),
    "chime": (
        f"sine=frequency=880:duration=0.5:sample_rate={SR}",
        "afade=t=in:d=0.01,afade=t=out:st=0.06:d=0.44",
    ),
}


# Every base blip is peak-normalised to this before the per-event gain is
# applied. Without it the recipes came out at wildly different levels (-11 to
# -18 dBFS peak depending on the lavfi source and its fades), so the dB gains
# in _events() meant nothing -- the whole SFX bus ended up ~30 dB under the
# voice and was simply inaudible in a finished video.
BASE_PEAK_DB = -6.0


def _peak_db(path: Path) -> float:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True)
    for line in r.stderr.splitlines():
        if "max_volume:" in line:
            return float(line.split("max_volume:")[1].split("dB")[0])
    return 0.0


def _ensure_base(force: bool = False) -> None:
    SFX_DIR.mkdir(parents=True, exist_ok=True)
    for name, (src, chain) in _RECIPES.items():
        out = SFX_DIR / f"{name}.wav"
        if out.exists() and not force:
            continue
        tmp = SFX_DIR / f"{name}.raw.wav"
        run_ffmpeg(["-f", "lavfi", "-i", src, "-af", f"{chain},aformat=sample_rates={SR}:channel_layouts=mono",
                    tmp], f"synth sfx {name}")
        gain = BASE_PEAK_DB - _peak_db(tmp)
        run_ffmpeg(["-i", tmp, "-af", f"volume={gain:.2f}dB",
                    "-ar", str(SR), "-ac", "1", out],
                   f"normalise sfx {name} ({gain:+.1f} dB)")
        tmp.unlink(missing_ok=True)


def _events(script, timeline: dict) -> list[tuple[float, str, float]]:
    """(time_seconds, sfx_name, gain_dB) for the whole video."""
    ev: list[tuple[float, str, float]] = []
    shots = timeline["shots"]
    beats = {b.id for b in script.beats}
    last_beat = script.beats[-1].id if script.beats else None
    seen_props: set[str] = set()
    prev_beat = None

    for i, s in enumerate(shots):
        t = s["start_s"]
        if i > 0 and getattr(config, "SFX_TICKS", False):
            ev.append((t, "tick", -20.0))
        if s["beat_id"] != prev_beat:
            if s["beat_id"] == last_beat:
                ev.append((t + 0.03, "chime", -12.0))
            prev_beat = s["beat_id"]
        # Pop on every beat that brings a new visual in -- including charts,
        # and including a prop the video has used before. Previously this
        # deduped by asset name across the WHOLE video, so a 5-minute
        # long-form got one accent every 10 seconds and most beats were
        # silent. What matters is that something new appeared on screen now,
        # not whether that icon was used earlier.
        if s["index"] == 0 and any(
                l["type"] in ("prop", "cutout", "chart") for l in s.get("layers", [])):
            # land it just BEFORE the beat, in the inter-beat gap. Fired at the
            # beat start it sat underneath the speech onset and was masked.
            ev.append((max(t - 0.06, 0.0), "pop", -12.0))

    ev.append((0.0, "whoosh", -8.0))
    return ev


def build_track(script, timeline: dict, out_wav: Path) -> Path | None:
    _ensure_base()
    events = _events(script, timeline)
    if not events:
        return None

    total = timeline["total_s"] + 0.3
    # The bed used to be literal digital silence (anullsrc). Raw TTS over dead
    # silence is a tell -- both to a listener and to the platforms' own audio
    # classifiers -- so it is now a very quiet filtered-noise room tone. Brown
    # noise rolled off hard at both ends gives warmth with no pitch, so it
    # never fights the voice or implies music (and it is synthesised, so
    # there is nothing to licence).
    amb_db = getattr(config, "SFX_AMBIENCE_DB", -26.0)
    if amb_db is None:
        inputs = ["-f", "lavfi", "-i", f"anullsrc=r={SR}:cl=stereo"]
        parts = [f"[0:a]atrim=0:{total:.3f}[bed]"]
    else:
        inputs = ["-f", "lavfi", "-i",
                  f"anoisesrc=d={total:.3f}:c=brown:r={SR}:a=0.7"]
        parts = [f"[0:a]highpass=f=45,lowpass=f=430,volume={amb_db}dB,"
                 f"afade=t=in:d=1.2,afade=t=out:st={max(total - 1.5, 0):.3f}:d=1.5,"
                 f"aformat=channel_layouts=stereo[bed]"]
    mix = ["[bed]"]

    for idx, (t, name, gain) in enumerate(events, start=1):
        inputs += ["-i", str(SFX_DIR / f"{name}.wav")]
        delay_ms = max(int(t * 1000), 0)
        parts.append(
            f"[{idx}:a]adelay={delay_ms}|{delay_ms},volume={gain}dB,"
            f"aformat=channel_layouts=stereo[s{idx}]")
        mix.append(f"[s{idx}]")

    parts.append(f"{''.join(mix)}amix=inputs={len(mix)}:normalize=0:"
                 f"duration=first,alimiter=limit=0.9[out]")
    run_ffmpeg(["-y", *inputs, "-filter_complex", ";".join(parts),
                "-map", "[out]", "-ar", str(SR), out_wav],
               f"sfx track ({len(events)} events)")
    return out_wav
