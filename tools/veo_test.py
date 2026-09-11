"""One-off: image-to-video via Vertex Veo, from a stickfin first-frame composite.

Proof-of-concept for the "Veo hook clip" hybrid idea -- NOT wired into the
pipeline. Reads a 9:16 first-frame PNG, animates it ~6-8s, writes an mp4.

    python tools/veo_test.py build/_veo_firstframe.png build/_veo_out.mp4
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stickfin import config

# Only the Veo 3.1 family is enabled on this GCP project (project-58f4f689...);
# veo-2.0 / veo-3.0 all 404. Confirmed in core-decor-automation's llms.txt.
#   veo-3.1-generate-001       Standard, audio-on, ~$0.75/s
#   veo-3.1-fast-generate-001  Fast, ~$0.15/s
#   veo-3.1-lite-generate-001  Lite, audio off, ~$0.05/s
MODEL = "veo-3.1-fast-generate-001"

PROMPT = (
    "2D hand-drawn stick-figure animation in a minimalist black-marker doodle "
    "style, exactly matching the drawing in the image: thin even black ink "
    "outlines, an open round white head with spiky hair, a skinny necktie, five "
    "thin lines for the body, on a flat cream paper background. The stick figure "
    "is an exasperated finance explainer talking straight to camera: it gestures "
    "with both arms, shrugs, leans in, throws its hands up, mouth moving as if "
    "ranting, eyebrows angled. Lively, snappy character motion. The camera is "
    "locked off -- no pan, no zoom, no dolly. Flat 2D, no 3D shading, no "
    "gradients, no lens effects. No on-screen text or captions."
)
NEGATIVE = ("photorealistic, 3d render, camera movement, zoom, text, captions, "
            "watermark, letterboxing, multiple characters, colour background")


def main() -> int:
    src = Path(sys.argv[1] if len(sys.argv) > 1 else "build/_veo_firstframe.png")
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "build/_veo_out.mp4")
    if not src.exists():
        raise SystemExit(f"no such first frame: {src}")

    from google import genai
    from google.genai import types

    client = genai.Client(vertexai=True, project=config.GCP_PROJECT,
                          location="us-central1")

    img = types.Image.from_file(location=str(src))
    cfg = types.GenerateVideosConfig(
        aspect_ratio="9:16",
        number_of_videos=1,
        duration_seconds=8,
        generate_audio=False,
        negative_prompt=NEGATIVE,
    )

    from google.genai import errors as genai_errors

    print(f"[veo] {MODEL}  first-frame={src}  8s 9:16, no audio")
    t0 = time.time()
    # this project has a small concurrent-Veo-job cap -> 429 RESOURCE_EXHAUSTED
    # thrown synchronously at submission. Back off and retry.
    op = None
    for attempt in range(8):
        try:
            op = client.models.generate_videos(model=MODEL, prompt=PROMPT, image=img, config=cfg)
            break
        except genai_errors.ClientError as e:
            if "429" not in str(e) and "RESOURCE_EXHAUSTED" not in str(e):
                raise
            wait = 30 * (attempt + 1)
            print(f"[veo]   429 on submit, retry in {wait}s ({attempt + 1}/8)")
            time.sleep(wait)
    if op is None:
        raise SystemExit("[veo] still rate-limited after retries")
    while not op.done:
        time.sleep(15)
        op = client.operations.get(op)
        print(f"[veo]   ... {time.time() - t0:.0f}s  done={op.done}")

    if op.error:
        raise SystemExit(f"[veo] FAILED: {op.error}")

    vids = op.response.generated_videos or []
    if not vids:
        raise SystemExit(f"[veo] no video in response: {op.response}")
    v = vids[0].video
    data = v.video_bytes or (client.files.download(file=v) and v.video_bytes)
    out.write_bytes(data)
    print(f"[veo] done in {time.time() - t0:.0f}s -> {out} ({out.stat().st_size/1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
