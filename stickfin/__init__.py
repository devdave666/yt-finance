"""stickfin -- a stick-figure short-video pipeline (Reels/Shorts first).

Stages (stickfin.pipeline runs them in order; each resumes from disk):
    audio     per-beat TTS / live-clip audio -> audio/*.wav, voiceover.wav, narration.json
    plan      timings -> timeline.json (shot list) + asset_plan.json   (no API calls)
    assets    asset_plan -> assets/**/*.png  (Nano Banana on Vertex + rembg cutouts)
    render    composite each shot (static camera, hard cuts) + mux VO/music/captions

Built to the vibe of Dev's reference reels: 9:16, thick-outline stick figures,
reusable pose/expression/background assets composited as layers, pose-to-pose
hard cuts on the beat, prominent synced captions. No Ken Burns, no tweening.
"""
__version__ = "0.2.0"
