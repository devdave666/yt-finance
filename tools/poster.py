"""Post the oldest queued short to every configured channel via Buffer.

No GCP/Vertex call anywhere in this path -- pure Buffer + git -- so the
posting cadence can't be broken by Vertex trial-credit expiry or quota once
tools/batch_build.py has filled state/queue.json.

    python tools/poster.py

Env: BUFFER_API_KEY (+ optional BUFFER_*_CHANNEL_ID overrides),
     GITHUB_REPOSITORY (set automatically in Actions).
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from stickfin import publish as publish_mod

REPO = Path(__file__).resolve().parent.parent


def _run(cmd: list) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(str(c) for c in cmd)} failed:\n{r.stderr[-1500:]}")


def main() -> int:
    entry = publish_mod.post_next_queued(REPO)
    if entry is None:
        print("[poster] queue is empty -- nothing to post "
             "(run the Batch Build workflow to refill it)")
        return 0

    print(f"[poster] posted {entry['slug']}: {entry['post_ids']}")
    _run(["git", "-C", str(REPO), "add", "state/queue.json"])
    _run(["git", "-C", str(REPO), "commit", "-m", f"post: {entry['slug']}"])
    _run(["git", "-C", str(REPO), "push"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
