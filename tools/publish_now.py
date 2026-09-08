"""Publish an already-committed Short straight to Buffer (YT + IG + TikTok),
bypassing state/queue.json entirely.

The normal path is enqueue() -> FIFO -> tools/poster.py drains one entry per
scheduled firing. That's the wrong tool for "publish this one now, live" --
with a real backlog in the queue (tens of entries), enqueuing would bury a
video behind everything already waiting rather than posting it.

This assumes `media/<slug>.mp4` is ALREADY committed and pushed (so it's
fetchable at raw.githubusercontent.com) -- it does no git operations itself,
just the three Buffer posts.

    python tools/publish_now.py --slug short-foo --title "..." \
        --description "..." [--no-instagram] [--no-tiktok]

Env: BUFFER_API_KEY (+ optional BUFFER_*_CHANNEL_ID overrides),
     GITHUB_REPOSITORY (set automatically in Actions).
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from stickfin import config, publish as publish_mod  # noqa: E402


def _hosted_url(slug: str) -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        remote = subprocess.run(["git", "-C", str(REPO), "remote", "get-url", "origin"],
                                capture_output=True, text=True).stdout.strip()
        m = remote.replace("git@github.com:", "").replace("https://github.com/", "")
        repo = m[:-4] if m.endswith(".git") else m
    branch = subprocess.run(["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True).stdout.strip() or "main"
    return f"https://raw.githubusercontent.com/{repo}/{branch}/media/{slug}.mp4"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--slug", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--no-instagram", action="store_true")
    ap.add_argument("--no-tiktok", action="store_true")
    args = ap.parse_args()

    if not (REPO / "media" / f"{args.slug}.mp4").exists():
        raise SystemExit(f"media/{args.slug}.mp4 not found -- commit + push it first")

    url = _hosted_url(args.slug)
    print(f"[publish-now] hosted: {url}")
    desc = args.description or args.title

    yt_id = publish_mod.buffer_post(url, desc, config.BUFFER_YOUTUBE_CHANNEL_ID,
                                    "youtube", title=args.title)
    print(f"[publish-now] YouTube post id: {yt_id}")

    plan = []
    if not args.no_instagram and config.BUFFER_INSTAGRAM_CHANNEL_ID:
        plan.append(("instagram", config.BUFFER_INSTAGRAM_CHANNEL_ID))
    if not args.no_tiktok and config.BUFFER_TIKTOK_CHANNEL_ID:
        plan.append(("tiktok", config.BUFFER_TIKTOK_CHANNEL_ID))
    for platform, channel in plan:
        try:
            pid = publish_mod.buffer_post(url, desc, channel, platform)
            print(f"[publish-now] {platform} post id: {pid}")
        except Exception as e:  # non-fatal: YouTube already went out
            print(f"[publish-now] {platform} post FAILED (non-fatal): {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
