"""One video, start to finish, no human input. The GitHub Actions cron runs this.

    generate -> audio -> plan -> assets -> render -> publish

Every stage is cached per build dir, so a re-run after a mid-way failure
resumes rather than re-spending. Set STICKFIN_AUTOPUBLISH=1 to actually post
(otherwise the video is built and committed but not sent to YouTube).
"""
from __future__ import annotations

import json
import sys
import traceback
from pathlib import Path

from stickfin import (assemble, assets as assets_mod, captions as captions_mod,
                      compositor, generate as generate_mod, publish as publish_mod,
                      script_model, timeline as timeline_mod, tts)

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
    out = assemble.mux(script, silent, script.build_dir / "voiceover.wav",
                       cap, script.music)
    print(f"       {out}")

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
