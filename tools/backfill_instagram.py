"""Post an already-built short to Instagram via Buffer, after the fact.

For shorts that went to YouTube before the Instagram path existed, or any run
where the Instagram post needs to be (re)done by hand. Reads the committed
metadata for the caption and rebuilds the same raw.githubusercontent.com URL
that publish.host_in_repo produced, then calls Buffer once.

    python tools/backfill_instagram.py <slug> [<slug> ...]
    python tools/backfill_instagram.py --list          # just print the channels

Env:
    BUFFER_API_KEY               required
    BUFFER_INSTAGRAM_CHANNEL_ID  the Instagram channel's Buffer id; if unset,
                                 the script lists channels and exits so you can
                                 grab it.
    GITHUB_REPOSITORY            owner/repo (auto-set in Actions; falls back to
                                 the origin remote locally).
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from stickfin import publish as publish_mod  # noqa: E402
BUFFER_URL = "https://api.buffer.com/graphql"

_CHANNELS_QUERY = """
query Channels {
  account {
    currentOrganization {
      channels { id service name }
    }
  }
}
"""


def _repo_slug() -> str:
    repo = os.environ.get("GITHUB_REPOSITORY")
    if repo:
        return repo
    remote = subprocess.run(["git", "-C", str(REPO), "remote", "get-url", "origin"],
                            capture_output=True, text=True).stdout.strip()
    m = remote.replace("git@github.com:", "").replace("https://github.com/", "")
    return m[:-4] if m.endswith(".git") else m


def _branch() -> str:
    return subprocess.run(["git", "-C", str(REPO), "rev-parse", "--abbrev-ref", "HEAD"],
                          capture_output=True, text=True).stdout.strip() or "main"


def list_channels(token: str) -> list[dict]:
    r = requests.post(BUFFER_URL,
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"},
                      json={"query": _CHANNELS_QUERY}, timeout=60)
    body = r.json()
    try:
        return body["data"]["account"]["currentOrganization"]["channels"]
    except (KeyError, TypeError):
        raise SystemExit(f"could not read channels from Buffer: {body}")


def caption_for(slug: str) -> str:
    meta_path = REPO / "scripts" / "auto" / f"{slug}.meta.json"
    if not meta_path.exists():
        raise SystemExit(f"no metadata at {meta_path}")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return meta.get("description") or meta.get("title") or slug


def main(argv: list[str]) -> int:
    token = os.environ.get("BUFFER_API_KEY")
    if not token:
        raise SystemExit("BUFFER_API_KEY is not set")

    if not argv or argv[0] == "--list":
        for c in list_channels(token):
            print(f"  {c['service']:12} {c['id']}  {c.get('name', '')}")
        return 0

    channel = os.environ.get("BUFFER_INSTAGRAM_CHANNEL_ID")
    if not channel:
        print("BUFFER_INSTAGRAM_CHANNEL_ID not set. Connected channels:")
        for c in list_channels(token):
            print(f"  {c['service']:12} {c['id']}  {c.get('name', '')}")
        raise SystemExit("set BUFFER_INSTAGRAM_CHANNEL_ID to the instagram id above and re-run")

    repo, branch = _repo_slug(), _branch()
    rc = 0
    for slug in argv:
        url = f"https://raw.githubusercontent.com/{repo}/{branch}/media/{slug}.mp4"
        # sanity: the media file must actually be committed
        if not (REPO / "media" / f"{slug}.mp4").exists():
            print(f"[{slug}] SKIP -- media/{slug}.mp4 not in the working tree")
            rc = 1
            continue
        try:
            post_id = publish_mod.buffer_post(url, caption_for(slug), channel, "instagram")
            print(f"[{slug}] Instagram post id: {post_id}")
        except Exception as e:  # noqa: BLE001
            print(f"[{slug}] FAILED: {e}")
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
