"""Pre-publish QA. Objective checks that BLOCK a bad short from going out, plus
an advisory Gemini "would I swipe" score that's logged but doesn't block.

check(script) -> QAResult(ok, blockers, warnings, critique)
"""
from __future__ import annotations

import json
import os
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .ffmpeg_util import probe_duration, run_ffmpeg


@dataclass
class QAResult:
    ok: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    critique: dict | None = None


def _img_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def _audio_stats(path: Path) -> tuple[float, float]:
    r = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", str(path),
         "-af", "volumedetect", "-f", "null", "-"],
        capture_output=True, text=True)
    mean = peak = 0.0
    for line in r.stderr.splitlines():
        if "mean_volume:" in line:
            mean = float(line.split("mean_volume:")[1].split("dB")[0])
        elif "max_volume:" in line:
            peak = float(line.split("max_volume:")[1].split("dB")[0])
    return mean, peak


def check(script, run_critique: bool = True) -> QAResult:
    bd = script.build_dir
    res = QAResult(ok=True)

    narration = json.loads((bd / "narration.json").read_text())
    timeline = json.loads((bd / "timeline.json").read_text())
    video = script.out_path

    # ---- duration ----
    # 16:9 long-form is a different product with a different acceptable length;
    # the Shorts window would reject every long-form video outright.
    lo_s, hi_s = (150.0, 1500.0) if script.fmt == "wide" else (12.0, 45.0)
    total = narration["total_s"]
    if not (lo_s <= total <= hi_s):
        res.blockers.append(
            f"length {total:.1f}s out of the {lo_s:.0f}-{hi_s:.0f}s window "
            f"for format {script.fmt!r}")

    if video.exists():
        vdur = probe_duration(video)
        if abs(vdur - total) > 0.6:
            res.blockers.append(f"video {vdur:.1f}s vs audio {total:.1f}s (>0.6s drift)")
        mean, peak = _audio_stats(video)
        if peak > -0.2:
            res.warnings.append(f"audio peaks at {peak:.1f}dB (clipping risk)")
        if mean < -28:
            res.warnings.append(f"audio quiet (mean {mean:.1f}dB)")
    else:
        res.blockers.append("no rendered video")

    # ---- pace consistency (the big one) ----
    spread = narration.get("wps_spread", 1.0)
    if spread > 1.8:
        wps = ", ".join(f"{b['id']}:{b['wps']}" for b in narration["beats"] if b.get("wps"))
        res.blockers.append(f"speech pace jumps around ({spread}x spread) -- {wps}")
    elif spread > 1.55:
        res.warnings.append(f"speech pace a bit uneven ({spread}x)")

    for b in narration["beats"]:
        w = b.get("wps")
        if w and (w < 2.0 or w > 3.9):
            res.warnings.append(f"beat {b['id']} at {w} wps (outside natural range)")
        if b.get("wps") and (b["speech_end_s"] - b["speech_start_s"]) < 0.25:
            res.blockers.append(f"beat {b['id']} has words but no detected speech")

    # ---- dead frames: a composite beat with nothing on screen but the host ----
    dead = sorted({s["beat_id"] for s in timeline["shots"]
                   if s.get("kind") == "composite" and s.get("layers")
                   and all(l["type"] == "character" for l in s["layers"])})
    if len(dead) >= 2:
        res.warnings.append(f"{len(dead)} talking-head beats with no prop/chart/headline "
                            f"({', '.join(dead)})")

    # ---- assets present ----
    adir = bd / "assets"
    for shot in timeline["shots"]:
        for layer in shot.get("layers", []):
            sub = {"character": "char", "prop": "prop", "cutout": "cutout",
                   "chart": "chart", "headline": "headline"}.get(layer["type"])
            if sub and not (adir / sub / f"{layer['asset']}.png").exists():
                res.blockers.append(f"missing {layer['type']} '{layer['asset']}' "
                                    f"(shot {shot['beat_id']})")

    # ---- geometry: nothing off-frame, nothing overlapping ----
    # Re-solves each shot from the REAL asset sizes (same inputs the compositor
    # used) and asserts the result is clean. layout.solve tries to guarantee
    # this, but its last-resort "shrink and hope" path can still leave a
    # collision on a crowded frame -- this is what catches that before publish
    # instead of a viewer catching it after.
    from . import layout
    geom_bad: list[str] = []
    for shot in timeline["shots"]:
        if shot.get("kind") != "composite":
            continue
        kinds, whs = [], []
        for layer in shot.get("layers", []):
            sub = {"character": "char", "prop": "prop", "cutout": "cutout",
                   "chart": "chart", "headline": "headline"}.get(layer["type"])
            p = (adir / sub / f"{layer['asset']}.png") if sub else None
            if p is None or not p.exists():
                continue
            kinds.append(layer["type"])
            whs.append(_img_size(p))
        if not kinds:
            continue
        boxes = layout.solve([{"type": k, "wh": wh} for k, wh in zip(kinds, whs)],
                             timeline.get("fmt", script.fmt))
        for problem in layout.audit(kinds, boxes, timeline.get("fmt", script.fmt)):
            geom_bad.append(f"{shot['beat_id']}#{shot['index']}: {problem}")
    if geom_bad:
        shown = "; ".join(geom_bad[:6])
        more = f" (+{len(geom_bad) - 6} more)" if len(geom_bad) > 6 else ""
        res.blockers.append(f"layout problems on {len(geom_bad)} shot(s): {shown}{more}")

    # ---- Gemini "would I swipe past this" pass ----
    # Advisory by default; only an *obviously broken* score blocks (the exact
    # failure the last reel shipped with: garbled fast speech = low clarity +
    # low pacing). Tunable so it can be loosened without a code change.
    floor = float(os.environ.get("STICKFIN_QA_CRITIQUE_FLOOR", "2"))
    min_overall = float(os.environ.get("STICKFIN_QA_MIN_OVERALL", "5"))
    if run_critique and video.exists():
        try:
            res.critique = _critique(video, timeline)
            (bd / "qa_critique.json").write_text(json.dumps(res.critique, indent=2))
            sc = res.critique.get("scores", {})
            probs = "; ".join(res.critique.get("top_problems", []))[:240]
            broken = {k: sc[k] for k in ("overall", "pacing", "clarity_of_audio", "visuals")
                      if isinstance(sc.get(k), (int, float)) and sc[k] <= floor}
            if broken:
                res.blockers.append(
                    "Gemini flags it broken: "
                    + ", ".join(f"{k} {v}/10" for k, v in broken.items()) + f" -- {probs}")
            elif isinstance(sc.get("overall"), (int, float)) and sc["overall"] < min_overall:
                res.blockers.append(
                    f"Gemini overall {sc['overall']}/10 (need >= {min_overall:g}) -- {probs}")
            for vf in res.critique.get("visual_defects", [])[:5]:
                res.warnings.append(f"visual: {vf}")
        except Exception as e:  # noqa: BLE001
            res.warnings.append(f"critique skipped: {str(e)[:120]}")

    res.blockers = sorted(set(res.blockers))
    res.warnings = sorted(set(res.warnings))
    res.ok = not res.blockers

    (bd / "qa.json").write_text(json.dumps(
        {"ok": res.ok, "blockers": res.blockers, "warnings": res.warnings,
         "critique_overall": (res.critique or {}).get("scores", {}).get("overall")},
        indent=2))
    return res


_CRIT_PROMPT = """You are reviewing a vertical finance Short before it's published.
You get the video (for pacing + audio) AND one full-resolution still per beat (for
visual detail). Judge both.

Check the stills carefully for these DEFECTS:
- a character pointing / gesturing / looking AWAY from the chart, prop, or headline
  it's talking about (it should face that element)
- a character with no mouth, or a blank/incomplete face
- any element clipped by the frame edge, or a figure standing off the floor
- two caption lines on screen at once, or a caption overlapping the art badly
- a chart whose numbers or labels don't match what's being said
- the stick figure drawn as a solid black blob instead of clean line art

Return ONLY JSON, no prose:
{"first_impression":"one honest line",
 "scores":{"hook":0-10,"pacing":0-10,"captions":0-10,"clarity_of_audio":0-10,"visuals":0-10,"overall":0-10},
 "visual_defects":["each concrete defect you actually see in the stills, with the beat number; [] if none"],
 "top_problems":["at most 3, concrete, most important first"]}"""


MAX_KEYFRAMES = 14


def _keyframes(video: Path, timeline: dict, out_dir: Path) -> list[Path]:
    """One still at the mid-point of each composite beat, capped at
    MAX_KEYFRAMES evenly spaced across the video.

    Vertex caps a whole inline request at roughly 20MB. A 25s Short has ~8
    beats and fits fine; a six-minute long-form has ~45, and sending one still
    each would blow the limit and lose the critique entirely (which fails
    open -- so it would silently publish unreviewed).
    """
    picks = [s for s in timeline["shots"] if s.get("kind") == "composite"]
    seen_ids, uniq = set(), []
    for s in picks:
        if s["beat_id"] not in seen_ids:
            seen_ids.add(s["beat_id"])
            uniq.append(s)
    if len(uniq) > MAX_KEYFRAMES:
        step = len(uniq) / MAX_KEYFRAMES
        uniq = [uniq[min(int(i * step), len(uniq) - 1)] for i in range(MAX_KEYFRAMES)]

    frames = []
    for shot in uniq:
        bid = shot["beat_id"]
        t = shot["start_s"] + shot["dur_s"] / 2
        fp = out_dir / f"kf_{bid}.jpg"
        try:
            run_ffmpeg(["-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
                        "-vf", "scale=720:-2", "-q:v", "3", fp],
                       f"qa keyframe {bid}")
            frames.append(fp)
        except Exception:  # noqa: BLE001
            pass
    return frames


def _critique(video: Path, timeline: dict | None = None) -> dict:
    from google import genai
    from google.genai import types

    model = os.environ.get("STICKFIN_QA_CRITIQUE_MODEL", "gemini-2.5-pro")

    # Vertex inline-data caps the whole request ~20MB; a CRF-18 short can brush
    # that. Send a small proxy for motion/audio + full-res stills for detail.
    proxy = video.with_suffix(".qa.mp4")
    kf_dir = video.parent / "_qa_kf"
    kf_dir.mkdir(exist_ok=True)
    parts: list = []
    try:
        run_ffmpeg(["-i", video, "-vf", "scale=-2:640", "-c:v", "libx264",
                    "-crf", "34", "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k",
                    proxy], "qa proxy")
        parts.append(types.Part.from_bytes(data=proxy.read_bytes(), mime_type="video/mp4"))
    except Exception:  # noqa: BLE001
        parts.append(types.Part.from_bytes(data=video.read_bytes(), mime_type="video/mp4"))
    finally:
        proxy.unlink(missing_ok=True)

    frames = _keyframes(video, timeline, kf_dir) if timeline else []
    for fp in frames:
        parts.append(types.Part.from_bytes(data=fp.read_bytes(), mime_type="image/jpeg"))
        fp.unlink(missing_ok=True)
    try:
        kf_dir.rmdir()
    except OSError:
        pass
    parts.append(f"{_CRIT_PROMPT}\n\n(The {len(frames)} stills are sampled evenly "
                 f"across the video, in playback order.)")

    client = genai.Client(vertexai=True, project=config.GCP_PROJECT,
                          location="us-central1")
    last = None
    for attempt in range(4):
        try:
            r = client.models.generate_content(
                model=model, contents=parts,
                config=types.GenerateContentConfig(response_mime_type="application/json"))
            return json.loads(r.text)
        except Exception as e:  # noqa: BLE001 -- retry only on rate limits
            last = e
            if "429" not in str(e) and "RESOURCE_EXHAUSTED" not in str(e):
                raise
            time.sleep(20 * (attempt + 1))
    raise last
