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

# Buffer's channel list lives in different places depending on how the token is
# scoped -- try the known shapes in order.
_CHANNELS_QUERIES = [
    "query { channels { id service name } }",
    "query { channels(input: {}) { id service name } }",
    "query { account { currentOrganization { channels { id service name } } } }",
    "query { viewer { channels { id service name } } }",
    "query { account { channels { id service name } } }",
]

_INTROSPECT = ('query { __schema { queryType { fields { name args { name } '
               'type { name kind ofType { name kind } } } } } }')


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


def _gql(token: str, query: str) -> dict:
    r = requests.post(BUFFER_URL,
                      headers={"Authorization": f"Bearer {token}",
                               "Content-Type": "application/json"},
                      json={"query": query}, timeout=60)
    return r.json()


def _dig_channels(obj) -> list[dict]:
    """Find the first list of {id, service, ...} dicts anywhere in a response."""
    if isinstance(obj, list):
        if obj and isinstance(obj[0], dict) and "id" in obj[0] and "service" in obj[0]:
            return obj
        for x in obj:
            got = _dig_channels(x)
            if got:
                return got
    elif isinstance(obj, dict):
        for v in obj.values():
            got = _dig_channels(v)
            if got:
                return got
    return []


def _rest_profiles(token: str) -> tuple[list[dict], str]:
    """Buffer's legacy v1 REST -- personal tokens that can't read org channels
    over GraphQL can usually still list profiles here."""
    for base in ("https://api.bufferapp.com/1/profiles.json",
                 "https://api.buffer.com/1/profiles.json"):
        try:
            r = requests.get(base, params={"access_token": token}, timeout=60)
            data = r.json()
        except Exception as e:  # noqa: BLE001
            return [], f"  GET {base} -> {e}"
        if isinstance(data, list):
            return ([{"id": p.get("id"), "service": p.get("service"),
                      "name": p.get("formatted_username") or p.get("service_username")}
                     for p in data], "")
        return [], f"  GET {base} -> {data}"
    return [], ""


def list_channels(token: str) -> list[dict]:
    attempts = []
    for q in _CHANNELS_QUERIES:
        body = _gql(token, q)
        chans = _dig_channels(body.get("data"))
        if chans:
            return chans
        attempts.append(f"  {q}\n    -> {body}")
    rest, err = _rest_profiles(token)
    if rest:
        return rest
    if err:
        attempts.append(err)
    schema = _gql(token, _INTROSPECT)
    try:
        fields = schema["data"]["__schema"]["queryType"]["fields"]
        attempts.append("  root query fields available to this token: "
                        + ", ".join(sorted(f["name"] for f in fields)))
    except (KeyError, TypeError):
        attempts.append(f"  introspection -> {schema}")
    raise SystemExit("could not read channels from Buffer. Tried:\n" + "\n".join(attempts))


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
