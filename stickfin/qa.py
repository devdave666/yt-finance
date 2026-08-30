"""Pre-publish QA. Objective checks that BLOCK a bad short from going out, plus
an advisory Gemini "would I swipe" score that's logged but doesn't block.

check(script) -> QAResult(ok, blockers, warnings, critique)
"""
from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from . import config
from .ffmpeg_util import probe_duration


@dataclass
class QAResult:
    ok: bool
    blockers: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    critique: dict | None = None


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
    total = narration["total_s"]
    if not (12.0 <= total <= 45.0):
        res.blockers.append(f"length {total:.1f}s out of the 12-45s window")

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

    # ---- Gemini "would I swipe past this" pass ----
    # Advisory by default; only an *obviously broken* score blocks (the exact
    # failure the last reel shipped with: garbled fast speech = low clarity +
    # low pacing). Tunable so it can be loosened without a code change.
    floor = float(os.environ.get("STICKFIN_QA_CRITIQUE_FLOOR", "2"))
    if run_critique and video.exists():
        try:
            res.critique = _critique(video)
            (bd / "qa_critique.json").write_text(json.dumps(res.critique, indent=2))
            sc = res.critique.get("scores", {})
            broken = {k: sc[k] for k in ("overall", "pacing", "clarity_of_audio")
                      if isinstance(sc.get(k), (int, float)) and sc[k] <= floor}
            if broken:
                res.blockers.append(
                    "Gemini flags it broken: "
                    + ", ".join(f"{k} {v}/10" for k, v in broken.items())
                    + f" -- {'; '.join(res.critique.get('top_problems', []))[:200]}")
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


_CRIT_PROMPT = """Watch this vertical finance Short as a viewer scrolling. Return ONLY JSON:
{"first_impression":"one honest line","scores":{"hook":0-10,"pacing":0-10,"captions":0-10,"clarity_of_audio":0-10,"overall":0-10},
 "top_problems":["at most 3, concrete"]}
No prose outside the JSON."""


def _critique(video: Path) -> dict:
    from google import genai
    from google.genai import types

    # Vertex inline-data caps the whole request ~20MB; a CRF-18 short can brush
    # that. Send a small proxy -- audio is untouched so pacing/clarity still read.
    proxy = video.with_suffix(".qa.mp4")
    try:
        run_ffmpeg(["-i", video, "-vf", "scale=-2:640", "-c:v", "libx264",
                    "-crf", "34", "-preset", "veryfast", "-c:a", "aac", "-b:a", "96k",
                    proxy], "qa proxy")
        data = proxy.read_bytes()
    except Exception:  # noqa: BLE001
        data = video.read_bytes()
    finally:
        proxy.unlink(missing_ok=True)

    client = genai.Client(vertexai=True, project=config.GCP_PROJECT,
                          location="us-central1")
    r = client.models.generate_content(
        model="gemini-2.5-flash",
        contents=[types.Part.from_bytes(data=data, mime_type="video/mp4"), _CRIT_PROMPT],
        config=types.GenerateContentConfig(response_mime_type="application/json"))
    return json.loads(r.text)
