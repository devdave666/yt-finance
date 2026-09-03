"""Pre-publish QA. Objective checks that BLOCK a bad short from going out, plus
an advisory Gemini "would I swipe" score that's logged but doesn't block.

check(script) -> QAResult(ok, blockers, warnings, critique)
"""
from __future__ import annotations

import json
import os
import re
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


def caption_extras(script, build_dir: Path) -> list[str]:
    """Words burned into the captions that are NOT anywhere in the script.

    Ground truth for "is there stray text on screen". Should always be empty --
    captions are generated from beat.say -- so anything here is a real bug
    (a leaked prompt, a mis-parsed line, a stale caption file). It also lets us
    tell the critique model this is already verified: it has twice invented
    "stray caption lines" quoting words from the TTS *style prompt* on frames
    that were provably clean, and that false positive is severe enough to
    block an otherwise good video.
    """
    ass = build_dir / "captions.ass"
    if not ass.exists():
        return []
    allowed: set[str] = set()
    for b in script.beats:
        allowed |= set(re.findall(r"[a-z']+", (b.say or "").lower()))
    for extra in (script.title, script.title_card):
        if extra:
            allowed |= set(re.findall(r"[a-z']+", extra.lower()))
    seen: set[str] = set()
    for line in ass.read_text(encoding="utf-8").splitlines():
        if not line.startswith("Dialogue") or ",," not in line:
            continue
        txt = re.sub(r"\{[^}]*\}", "", line.split(",,", 1)[1]).replace("\\N", " ")
        seen |= set(re.findall(r"[a-z']+", txt.lower()))
    return sorted(seen - allowed)


def _img_size(path: Path) -> tuple[int, int]:
    from PIL import Image
    with Image.open(path) as im:
        return im.size


def _syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    n = len(re.findall(r"[aeiouy]+", w))
    if w.endswith("e") and not w.endswith("le"):
        n -= 1
    if w.endswith("le") and len(w) > 2 and w[-3] not in "aeiouy":
        n += 1
    return max(1, n)


def _reading_grade(text: str) -> tuple[float, int]:
    """Flesch-Kincaid grade level for the spoken narration, plus the word count.

    Bitton's rule from the podcast: keep a broad-audience Short at roughly a
    5th-8th grade reading level. This is a heuristic (approximate syllables),
    so it only ever warns -- it never blocks.
    """
    sentences = [s for s in re.split(r"[.!?]+", text) if s.strip()]
    words = re.findall(r"[A-Za-z']+", text)
    if not sentences or not words:
        return 0.0, 0
    syl = sum(_syllables(w) for w in words)
    grade = 0.39 * (len(words) / len(sentences)) + 11.8 * (syl / len(words)) - 15.59
    return round(grade, 1), len(words)


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

    # ---- reading level: keep the Short broad (Bitton: ~5th-8th grade) ----
    spoken = " ".join(b.say for b in script.beats if not b.id.startswith("cta"))
    grade, nwords = _reading_grade(spoken)
    if nwords >= 25 and grade > 9.0:
        res.warnings.append(
            f"narration reads at ~grade {grade} (aim grade 8 or under -- "
            f"shorter sentences, plainer words)")

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

    # ---- captions contain only scripted words ----
    cap_extra = caption_extras(script, bd)
    if cap_extra:
        res.blockers.append(
            f"captions contain {len(cap_extra)} word(s) that are not in the "
            f"script: {cap_extra[:8]}")

    # ---- Gemini "would I swipe past this" pass ----
    # Advisory by default; only an *obviously broken* score blocks (the exact
    # failure the last reel shipped with: garbled fast speech = low clarity +
    # low pacing). Tunable so it can be loosened without a code change.
    floor = float(os.environ.get("STICKFIN_QA_CRITIQUE_FLOOR", "2"))
    min_overall = float(os.environ.get("STICKFIN_QA_MIN_OVERALL", "5"))
    if run_critique and video.exists():
        try:
            res.critique = _critique(video, timeline,
                                     captions_verified=not cap_extra)
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


_CRIT_INTRO = {
    # Judging a 6-minute landscape explainer against Shorts criteria produces
    # nonsense feedback ("crams too much into one short", "pacing too fast" on
    # a deliberately unhurried read). The medium has to be stated.
    "short": "You are reviewing a vertical finance Short (under a minute, "
             "designed to stop a scrolling thumb) before it's published.",
    "wide": "You are reviewing a LONG-FORM 16:9 finance explainer (several "
            "minutes, watched deliberately on a big screen -- NOT a Short) "
            "before it's published. Judge it as a YouTube explainer: depth, "
            "clarity and a steady teaching pace are virtues here, and covering "
            "a topic thoroughly is the point, not a flaw. Do not penalise it "
            "for being longer or denser than a Short.",
}

# NB: substituted with str.replace, NOT str.format -- the JSON schema at the
# bottom is full of literal braces that format() would try to parse as fields.
_CRIT_PROMPT = """@INTRO@
You get the video (for pacing + audio) AND stills sampled across it (for
visual detail). Judge both.

Check the stills carefully for these DEFECTS:
- a character pointing / gesturing / looking AWAY from the chart, prop, or headline
  it's talking about (it should face that element)
- a character with no mouth, or a blank/incomplete face
- any element clipped by the frame edge, or a figure standing off the floor
- two caption lines on screen at once, or a caption overlapping the art badly
- a chart whose numbers or labels don't match what's being said
- the stick figure drawn as a solid black blob instead of clean line art

A deliberate red edge-vignette darkening the corners on SOME beats is intentional
(it marks a fee / loss / trap) -- do not report it as a defect or a colour problem.
A very quiet ambient tone/drone under the narration is an intentional bed, not a
hum or an artefact -- only flag audio if the SPOKEN VOICE itself is unclear.

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

    # Downscale to ~2/3 of the source width, not a fixed 720. On a 9:16 Short
    # (1080 wide) 720 was already 2/3; on 16:9 long-form (1920 wide) it was
    # 37%, which shrank burned-in subtitles to the point the critique started
    # confabulating text it could not actually read -- it reported "stray
    # caption lines" quoting words from the TTS style prompt, on frames that
    # were verifiably clean.
    src_w = timeline.get("fmt") == "wide" and 1920 or 1080
    tgt_w = int(src_w * 2 / 3) // 2 * 2

    frames = []
    for shot in uniq:
        bid = shot["beat_id"]
        t = shot["start_s"] + shot["dur_s"] / 2
        fp = out_dir / f"kf_{bid}.jpg"
        try:
            run_ffmpeg(["-ss", f"{t:.3f}", "-i", video, "-frames:v", "1",
                        "-vf", f"scale={tgt_w}:-2", "-q:v", "3", fp],
                       f"qa keyframe {bid}")
            frames.append(fp)
        except Exception:  # noqa: BLE001
            pass
    return frames


def _critique(video: Path, timeline: dict | None = None,
              captions_verified: bool = False) -> dict:
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
    verified = ""
    if captions_verified:
        # Hand over the one thing we can prove, because this model has twice
        # invented "stray caption lines" -- quoting words from the TTS style
        # prompt -- on frames that were verifiably clean, and scored the video
        # unpublishable over it.
        verified = ("\n\nALREADY VERIFIED PROGRAMMATICALLY (do not report these "
                    "as defects): every burned-in caption was generated from the "
                    "script and contains only scripted narration words -- there "
                    "is no stray, leaked, duplicated or production-note text "
                    "anywhere on screen. If a frame looks like it has extra "
                    "text, you are misreading it; ignore it.")
    fmt = (timeline or {}).get("fmt", "short")
    prompt = _CRIT_PROMPT.replace("@INTRO@", _CRIT_INTRO.get(fmt, _CRIT_INTRO["short"]))
    parts.append(f"{prompt}{verified}\n\n(The {len(frames)} stills are sampled "
                 f"evenly across the video, in playback order.)")

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
