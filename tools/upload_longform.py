"""Publish an already-committed video to YouTube via Buffer at a chosen privacy.

Separate from the Shorts queue/poster path on purpose: this is for one-off
long-form uploads (and for review copies that must NOT go out public).

    python tools/upload_longform.py --privacy-values          # what Buffer accepts
    python tools/upload_longform.py --slug <slug> --title T --description D \
        --privacy unlisted

Env: BUFFER_API_KEY (+ optional BUFFER_YOUTUBE_CHANNEL_ID override),
     GITHUB_REPOSITORY (set automatically in Actions).
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

import requests

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from stickfin import config, publish as publish_mod  # noqa: E402

BUFFER_URL = "https://api.buffer.com/graphql"


def _gql(token: str, query: str, variables: dict | None = None) -> dict:
    payload = {"query": query}
    if variables:
        payload["variables"] = variables
    r = requests.post(BUFFER_URL,
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"},
                      json=payload, timeout=60)
    return r.json()


def privacy_values(token: str) -> None:
    """Print every enum Buffer exposes that looks like a privacy setting.

    Worth checking before any real upload: this path uses mode=shareNow, and a
    sent post cannot be retracted. If an invalid privacy value were silently
    dropped, a review copy would go out PUBLIC.
    """
    body = _gql(token, "query { __schema { types { name kind enumValues { name } } } }")
    types = (((body.get("data") or {}).get("__schema") or {}).get("types")) or []
    if not types:
        print("introspection failed:", json.dumps(body)[:600])
        return
    hits = 0
    for t in types:
        if t.get("kind") != "ENUM" or not t.get("enumValues"):
            continue
        vals = [v["name"] for v in t["enumValues"]]
        name = (t.get("name") or "")
        if "privacy" in name.lower() or any(
                v.lower() in ("public", "private", "unlisted") for v in vals):
            print(f"{name}: {vals}")
            hits += 1
    if not hits:
        print("no privacy-looking enum found; dumping enum names:")
        print([t["name"] for t in types if t.get("kind") == "ENUM"][:80])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--privacy-values", action="store_true",
                    help="introspect and print accepted privacy enums, then exit")
    ap.add_argument("--slug", help="basename of the committed media/<slug>.mp4")
    ap.add_argument("--title")
    ap.add_argument("--description", default="")
    ap.add_argument("--privacy", default="unlisted")
    args = ap.parse_args()

    token = os.environ.get("BUFFER_API_KEY")
    if not token:
        raise SystemExit("BUFFER_API_KEY is not set")

    if args.privacy_values:
        privacy_values(token)
        return 0

    if not (args.slug and args.title):
        raise SystemExit("--slug and --title are required to upload")

    mp4 = REPO / "media" / f"{args.slug}.mp4"
    if not mp4.exists():
        raise SystemExit(f"{mp4} is not committed -- commit it first so Buffer "
                         f"can fetch it over https")

    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        remote = subprocess.run(["git", "-C", str(REPO), "remote", "get-url", "origin"],
                                capture_output=True, text=True).stdout.strip()
        m = remote.replace("git@github.com:", "").replace("https://github.com/", "")
        repo = m[:-4] if m.endswith(".git") else m
    url = f"https://raw.githubusercontent.com/{repo}/main/media/{args.slug}.mp4"

    print(f"[upload] {args.slug}  privacy={args.privacy}")
    print(f"[upload] source: {url}")
    pid = publish_mod.buffer_post(
        url, args.description, config.BUFFER_YOUTUBE_CHANNEL_ID, "youtube",
        title=args.title, privacy=args.privacy)
    print(f"[upload] Buffer post id: {pid}")
    print("[upload] NOTE: confirm the privacy setting in YouTube Studio -- a "
          "Buffer shareNow post cannot be retracted.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
