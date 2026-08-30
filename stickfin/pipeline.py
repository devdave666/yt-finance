"""Orchestrator / CLI.

    python -m stickfin.pipeline scripts/skit_doctor_glasses.yaml
    python -m stickfin.pipeline <script.yaml> --stage assets
    python -m stickfin.pipeline <script.yaml> --from assets
    python -m stickfin.pipeline <script.yaml> --only b1,b2       # cheap slice
    python -m stickfin.pipeline <script.yaml> --music assets/lofi.mp3 --force

Stages (each reads the previous stage's files off disk, so you can resume):
    audio -> plan -> assets -> render
"""
from __future__ import annotations

import argparse
import json
import sys

from . import assemble, assets as assets_mod, captions as captions_mod
from . import compositor, script_model, sfx as sfx_mod, timeline as timeline_mod, tts

STAGES = ["audio", "plan", "assets", "render"]


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="stickfin")
    ap.add_argument("script")
    ap.add_argument("--stage", choices=["all", *STAGES], default="all")
    ap.add_argument("--from", dest="from_stage", choices=STAGES)
    ap.add_argument("--only", help="comma-separated beat ids (cheap style test)")
    ap.add_argument("--music", help="background music file (ducked under VO)")
    ap.add_argument("--no-captions", action="store_true")
    ap.add_argument("--no-sfx", action="store_true", help="skip sound effects")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)

    script = script_model.load_script(args.script)

    if args.only:
        keep = [b.strip() for b in args.only.split(",") if b.strip()]
        unknown = [b for b in keep if b not in {x.id for x in script.beats}]
        if unknown:
            ap.error(f"--only: no such beat id(s): {unknown}")
        script.beats = [b for b in script.beats if b.id in keep]
        script.slug = f"{script.slug}__only-{'-'.join(keep)}"
        print(f"[only] {len(script.beats)} beat(s) -> build/{script.slug}")

    script.build_dir.mkdir(parents=True, exist_ok=True)

    if args.from_stage:
        want = set(STAGES[STAGES.index(args.from_stage):])
    elif args.stage == "all":
        want = set(STAGES)
    else:
        want = {args.stage}

    narration = timeline = None

    if "audio" in want:
        print("[audio] synthesizing / slicing per-beat audio ...")
        narration = tts.synthesize(script, force=args.force)
        print(f"        {narration['total_s']:.1f}s over {len(narration['beats'])} beats")

    if "plan" in want:
        if narration is None:
            narration = json.loads((script.build_dir / "narration.json").read_text())
        print("[plan] timeline + asset plan ...")
        timeline = timeline_mod.plan(script, narration)
        aplan = assets_mod.plan_assets(script)
        print(f"       {timeline['shot_count']} shots, {aplan['count']} assets, "
              f"{timeline['total_s']:.1f}s")

    if "assets" in want:
        aplan = json.loads((script.build_dir / "asset_plan.json").read_text())
        print("[assets] generating art (Vertex) + cutting out (rembg) ...")
        assets_mod.generate_assets(script, aplan, force=args.force)

    if "render" in want:
        if timeline is None:
            timeline = json.loads((script.build_dir / "timeline.json").read_text())
        print("[render] compositing shots ...")
        silent = compositor.render_shots(script, timeline)
        cap = None
        if not args.no_captions:
            cap = captions_mod.build(script, script.build_dir / "captions.ass")
        sfx_track = None
        if not args.no_sfx:
            sfx_track = sfx_mod.build_track(script, timeline,
                                            script.build_dir / "sfx.wav")
        print("[render] mux ...")
        out = assemble.mux(script, silent, script.build_dir / "voiceover.wav",
                           cap, args.music or script.music, sfx=sfx_track)
        print(f"[done] {out}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
