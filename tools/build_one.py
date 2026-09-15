"""One-off: build (but don't publish) a specific already-written script,
mirroring run_daily.py's stages without spending another generate() call.

    python -m tools.build_one scripts/auto/<slug>.yaml
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

from stickfin import (assemble, assets as assets_mod, captions as captions_mod,
                      compositor, music as music_mod, qa as qa_mod,
                      script_model, sfx as sfx_mod, timeline as timeline_mod, tts)


def main() -> int:
    script_path = Path(sys.argv[1])
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
    bed, bed_db = script.music, None
    if not bed:
        bed, bed_db = music_mod.resolve_bed(script, narration, script.build_dir)
    out = assemble.mux(script, silent, script.build_dir / "voiceover.wav",
                       cap, str(bed) if bed else None, sfx=sfx_track,
                       music_db=bed_db)
    print(f"       {out}")

    print("[qa]")
    report = qa_mod.check(script, run_critique=False)
    for w in report.warnings:
        print(f"       warn: {w}")
    if not report.ok:
        for b in report.blockers:
            print(f"       BLOCK: {b}")
        print("[qa] failed QA")
        return 2
    print("[qa] passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
