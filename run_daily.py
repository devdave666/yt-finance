"""One video, start to finish, no human input. The GitHub Actions cron runs this.

    generate -> audio -> plan -> assets -> render -> publish

Every stage is cached per build dir, so a re-run after a mid-way failure
resumes rather than re-spending. Set STICKFIN_AUTOPUBLISH=1 to actually post
(otherwise the video is built and committed but not sent to YouTube).
"""
from __future__ import annotations

import json
import os
import sys
import traceback
from pathlib import Path

from stickfin import (assemble, assets as assets_mod, captions as captions_mod,
                      compositor, config, generate as generate_mod,
                      publish as publish_mod, qa as qa_mod, script_model,
                      sfx as sfx_mod, timeline as timeline_mod, tts)

REPO = Path(__file__).resolve().parent


def main() -> int:
    script_path, meta = generate_mod.generate()
    script = script_model.load_script(script_path)
    script.build_dir.mkdir(parents=True, exist_ok=True)

    print("[audio]")
    narration = tts.synthesize(script)

    print("[plan]")
    tl = timeline_mod.plan(script, narration)
    assets_mod.plan_assets(script)
    print(f"       {tl['shot_count']} shots, {tl['total_s']:.1f}s")

    print("[assets]")
    aplan = json.loads((script.build_dir / "asset_plan.json").read_text())
    assets_mod.generate_assets(script, aplan)

    print("[render]")
    silent = compositor.render_shots(script, tl)
    cap = captions_mod.build(script, script.build_dir / "captions.ass")
    sfx_track = sfx_mod.build_track(script, tl, script.build_dir / "sfx.wav")
    bed = script.music
    if not bed and script.fmt == "wide" and config.AMBIENT_BED:
        bed = str(sfx_mod.build_bed(tl["total_s"], script.build_dir / "bed.wav"))
    out = assemble.mux(script, silent, script.build_dir / "voiceover.wav",
                       cap, bed, sfx=sfx_track)
    print(f"       {out}")

    print("[qa]")
    autopublish = os.environ.get("STICKFIN_AUTOPUBLISH") == "1"
    report = qa_mod.check(script, run_critique=autopublish)
    for w in report.warnings:
        print(f"       warn: {w}")
    if report.critique:
        sc = report.critique.get("scores", {})
        print(f"       critique {sc.get('overall')}/10 "
              f"(hook {sc.get('hook')}, pace {sc.get('pacing')}, "
              f"caps {sc.get('captions')}, audio {sc.get('clarity_of_audio')}, "
              f"visuals {sc.get('visuals')}) "
              f"-- {report.critique.get('first_impression', '')}")
        for p in report.critique.get("top_problems", []):
            print(f"         - {p}")
    if not report.ok:
        for b in report.blockers:
            print(f"       BLOCK: {b}")
        print("[qa] failed QA -- NOT publishing. Build kept in the artifact for review.")
        return 2

    print("[publish]")
    result = publish_mod.publish(script, meta, REPO)
    (script.build_dir / "publish.json").write_text(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:
        traceback.print_exc()
        sys.exit(1)
