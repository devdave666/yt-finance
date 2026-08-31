"""Build N shorts end-to-end and queue every one that passes QA for later
posting. No social publish happens here -- see tools/poster.py for that.

    python tools/batch_build.py --count 15

Each short that clears QA is hosted (committed to media/ + scripts/auto/)
and appended to state/queue.json immediately, one git push per short, same
as the normal daily pipeline -- so a mid-batch crash never loses already-
finished work. A short that fails QA is skipped (left uncommitted, build
kept only in the workflow artifact) and the loop moves on; one bad take
doesn't stall the whole batch.
"""
from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stickfin import (assemble, assets as assets_mod, captions as captions_mod,
                      compositor, generate as generate_mod, publish as publish_mod,
                      qa as qa_mod, script_model, sfx as sfx_mod,
                      timeline as timeline_mod, tts)

REPO = Path(__file__).resolve().parent.parent


def build_one() -> tuple[bool, str]:
    script_path, meta = generate_mod.generate()
    script = script_model.load_script(script_path)
    script.build_dir.mkdir(parents=True, exist_ok=True)

    print("  [audio]")
    narration = tts.synthesize(script)

    print("  [plan]")
    tl = timeline_mod.plan(script, narration)
    assets_mod.plan_assets(script)

    print("  [assets]")
    aplan = json.loads((script.build_dir / "asset_plan.json").read_text())
    assets_mod.generate_assets(script, aplan)

    print("  [render]")
    silent = compositor.render_shots(script, tl)
    cap = captions_mod.build(script, script.build_dir / "captions.ass")
    sfx_track = sfx_mod.build_track(script, tl, script.build_dir / "sfx.wav")
    assemble.mux(script, silent, script.build_dir / "voiceover.wav", cap, script.music,
                sfx=sfx_track)

    print("  [qa]")
    report = qa_mod.check(script, run_critique=True)
    for w in report.warnings:
        print(f"    warn: {w}")
    if not report.ok:
        print(f"    BLOCK: {'; '.join(report.blockers)}")
        return False, script.slug

    publish_mod.enqueue(script, meta, REPO)
    return True, script.slug


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--count", type=int, default=15, help="shorts to queue")
    ap.add_argument("--max-attempts", type=int, default=None,
                    help="hard cap on attempts (built + QA-failed); default 2x count")
    args = ap.parse_args()
    max_attempts = args.max_attempts or args.count * 2

    built, attempts, failures = 0, 0, []
    while built < args.count and attempts < max_attempts:
        attempts += 1
        print(f"\n=== short {built + 1}/{args.count} (attempt {attempts}/{max_attempts}) ===")
        try:
            ok, slug = build_one()
        except Exception:
            traceback.print_exc()
            ok, slug = False, "(crashed before a slug was known)"
        if ok:
            built += 1
            print(f"  queued: {slug}")
        else:
            failures.append(slug)
            print(f"  skipped: {slug}")

    print(f"\n=== batch done: {built}/{args.count} queued in {attempts} attempts ===")
    if failures:
        print("skipped:\n  " + "\n  ".join(failures))
    return 0 if built == args.count else 1


if __name__ == "__main__":
    sys.exit(main())
