"""Optional: animate the beat-1 hook shot with Veo 3.1 image-to-video instead
of a static composite. Gated by config.VEO_HOOK (STICKFIN_VEO_HOOK=1, off by
default) -- costs real money (~$1/clip on Fast) and is best-effort: any
failure prints a warning and leaves the already-rendered static clip in
place, never blocks the build.

Reuses the ALREADY-COMPOSITED beat-1 clip as the Veo first-frame reference
(grabbed post pop-in, ~0.3s in) instead of rebuilding the layout separately --
guarantees the animated clip opens on exactly what the layout solver placed
(headline position, host pose, brand background), not an approximation of it.

Only the Veo 3.1 `-001` family is enabled on this GCP project -- veo-2.0-*,
veo-3.0-*, and any `-preview` suffix 404 ("no access") even though the
project genuinely has Veo 3.1. See memory reference-veo-vertex.md for the
full gotcha list (429-at-submit quota, don't probe with real payloads, etc).
"""
from __future__ import annotations

import time
from pathlib import Path

from . import config
from .ffmpeg_util import run_ffmpeg

VEO_CLIP_S = 8          # Veo's max single-clip length; trimmed/held to fit the shot
VEO_TIMEOUT_S = 360

NEGATIVE_PROMPT = (
    "photorealistic, 3d render, camera movement, pan, zoom, dolly, text, "
    "captions, subtitles, watermark, letterboxing, colour background, "
    "second character, extra limb, changing outfit"
)

MOTION_PROMPT = (
    "2D hand-drawn stick-figure animation in a minimalist black-marker doodle "
    "style, exactly matching the drawing in the image: thin even black ink "
    "outlines, an open round white head with spiky hair, a skinny necktie, "
    "five thin lines for the body, on a flat cream paper background. The "
    "figure is a finance explainer talking straight to camera -- it gestures "
    "with both arms, leans in, shrugs, reacts with its whole body, mouth "
    "moving as it speaks. Lively character motion, same single figure the "
    "whole time, same outfit, same proportions throughout. The camera is "
    "locked off -- no pan, no zoom, no dolly. Flat 2D, no 3D shading, no "
    "gradients. No on-screen text, no captions, no second character."
)


def try_replace_hook(script, timeline: dict, clip_path: Path) -> bool:
    """Best-effort: animate the beat-1 hook shot in place.

    Returns True if `clip_path` was replaced with a Veo-animated version;
    False (never raises) if it fell back to the static composite already
    sitting there, for any reason -- wrong shot, wrong format, or a failed
    generation.
    """
    if script.fmt != "short" or not timeline["shots"] or not script.beats:
        return False
    shot = timeline["shots"][0]
    if shot["beat_id"] != script.beats[0].id or shot.get("kind") != "composite":
        return False
    if not any(l.get("type") == "headline" for l in shot["layers"]):
        return False   # only the hook shot is worth the spend

    try:
        fitted = _generate(clip_path, shot["dur_s"])
    except Exception as e:  # noqa: BLE001 -- experimental + real money, never block the build
        print(f"  [veo] hook clip skipped ({str(e)[:160]}) -- keeping the static composite")
        return False

    clip_path.write_bytes(fitted.read_bytes())
    fitted.unlink(missing_ok=True)
    print(f"  [veo] hook clip: {clip_path.name}")
    return True


def _generate(static_clip: Path, target_s: float) -> Path:
    from google import genai
    from google.genai import errors as genai_errors
    from google.genai import types

    first_frame = static_clip.with_suffix(".veo_ff.png")
    run_ffmpeg(["-ss", "0.3", "-i", static_clip, "-frames:v", "1", first_frame],
               "veo hook first-frame")

    client = genai.Client(vertexai=True, project=config.GCP_PROJECT, location="us-central1")
    img = types.Image.from_file(location=str(first_frame))
    cfg = types.GenerateVideosConfig(
        aspect_ratio="9:16", number_of_videos=1, duration_seconds=VEO_CLIP_S,
        generate_audio=False, negative_prompt=NEGATIVE_PROMPT)

    # This project's small concurrent-Veo-job quota throws 429 synchronously
    # at submission, before there's even an operation to poll.
    op = None
    for attempt in range(6):
        try:
            op = client.models.generate_videos(
                model=config.VEO_MODEL, prompt=MOTION_PROMPT, image=img, config=cfg)
            break
        except genai_errors.ClientError as e:
            rate_limited = "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)
            if not rate_limited or attempt == 5:
                raise
            time.sleep(20 * (attempt + 1))
    first_frame.unlink(missing_ok=True)

    t0 = time.time()
    while not op.done:
        if time.time() - t0 > VEO_TIMEOUT_S:
            raise RuntimeError("veo generation timed out")
        time.sleep(10)
        op = client.operations.get(op)
    if getattr(op, "error", None):
        raise RuntimeError(f"veo error: {op.error}")
    vids = op.response.generated_videos or []
    if not vids:
        raise RuntimeError(f"veo returned no video: {op.response!r}"[:200])

    raw = static_clip.with_suffix(".veo_raw.mp4")
    raw.write_bytes(vids[0].video.video_bytes)

    # Veo's own output canvas (720x1280 for 9:16) isn't necessarily ours;
    # scale/crop to match, hit the pipeline fps, and hold the last frame if
    # the beat needs more screen time than one Veo clip gives (max 8s).
    cw, ch = config.canvas("short")
    fitted = static_clip.with_suffix(".veo_fit.mp4")
    run_ffmpeg(
        ["-i", raw, "-vf", f"scale={cw}:{ch}:force_original_aspect_ratio=increase,"
                           f"crop={cw}:{ch},setsar=1,fps={config.FPS},"
                           f"tpad=stop_mode=clone:stop_duration=2",
         "-t", f"{target_s:.3f}", "-an", "-c:v", "libx264", "-preset", "medium",
         "-crf", "18", "-pix_fmt", "yuv420p", fitted],
        "veo hook fit-to-shot")
    raw.unlink(missing_ok=True)
    return fitted
