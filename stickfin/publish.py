"""Publish the finished short to YouTube and Instagram.

Mirrors core-decor-automation's proven path: commit the mp4 into the repo so
it's served from raw.githubusercontent.com, then hand that URL to Buffer's
GraphQL API with mode=shareNow. Buffer is used instead of the platforms' own
APIs because YouTube's OAuth verification and Instagram's Graph API setup are
high-friction for an automated poster (same call core-decor made).

Both destinations go through one Buffer account; each is a separate connected
channel addressed by its own channel id. Instagram video posts land as Reels.

Env:
    BUFFER_API_KEY
    BUFFER_YOUTUBE_CHANNEL_ID
    BUFFER_INSTAGRAM_CHANNEL_ID   (optional -- skipped if unset)
    GITHUB_REPOSITORY   (owner/repo, set automatically in Actions)
    STICKFIN_AUTOPUBLISH=1  to actually post (otherwise host-only, dry run)
"""
from __future__ import annotations

import datetime as dt
import json
import os
import subprocess
from pathlib import Path

from . import config

YT_CATEGORY_EDUCATION = "27"
QUEUE_PATH = Path("state/queue.json")

_CREATE_POST = """
mutation CreatePost($input: CreatePostInput!) {
  createPost(input: $input) {
    __typename
    ... on PostActionSuccess { post { id status } }
    ... on InvalidInputError { message }
    ... on LimitReachedError { message }
    ... on UnauthorizedError { message }
    ... on UnexpectedError { message }
    ... on RestProxyError { message }
    ... on NotFoundError { message }
  }
}
"""


def _run(cmd: list[str]) -> None:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} failed:\n{r.stderr[-1500:]}")


def host_in_repo(video: Path, slug: str, repo_root: Path) -> str:
    """Copy the video into media/, commit + push, return its raw URL."""
    media = repo_root / "media"
    media.mkdir(exist_ok=True)
    dest = media / f"{slug}.mp4"
    dest.write_bytes(Path(video).read_bytes())

    _run(["git", "-C", str(repo_root), "add", "media", "state", "scripts/auto"])
    _run(["git", "-C", str(repo_root), "commit", "-m", f"auto: {slug}"])
    _run(["git", "-C", str(repo_root), "push"])

    repo = os.environ.get("GITHUB_REPOSITORY")
    if not repo:
        remote = subprocess.run(["git", "-C", str(repo_root), "remote", "get-url", "origin"],
                                capture_output=True, text=True).stdout.strip()
        m = remote.replace("git@github.com:", "").replace("https://github.com/", "")
        repo = m[:-4] if m.endswith(".git") else m
    branch = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "--abbrev-ref", "HEAD"],
                            capture_output=True, text=True).stdout.strip() or "main"
    return f"https://raw.githubusercontent.com/{repo}/{branch}/media/{slug}.mp4"


def buffer_post(video_url: str, text: str, channel_id: str, platform: str,
                *, title: str | None = None) -> str:
    """Create a shareNow post on one Buffer channel; return the Buffer post id.

    platform is only used for YouTube's extra required fields and for error
    messages -- Buffer routes by channel_id, not by this string.
    """
    import requests

    token = os.environ["BUFFER_API_KEY"]
    asset: dict = {"video": {"url": video_url}}
    post_input: dict = {
        "channelId": channel_id,
        "mode": "shareNow",
        "schedulingType": "automatic",
        "needsApproval": False,
        "text": text,
        "assets": [asset],
    }
    if platform == "youtube":
        if not title:
            raise RuntimeError("title is required when publishing to YouTube via Buffer")
        asset["video"]["metadata"] = {"title": title}
        post_input["metadata"] = {"youtube": {"title": title,
                                              "categoryId": YT_CATEGORY_EDUCATION,
                                              "privacy": "public"}}
    elif platform == "instagram":
        # a video post to IG is a Reel; Buffer rejects it without an explicit type
        post_input["metadata"] = {"instagram": {"type": "reel",
                                                "shouldShareToFeed": True}}

    r = requests.post(
        "https://api.buffer.com/graphql",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": _CREATE_POST, "variables": {"input": post_input}}, timeout=60)
    body = r.json()
    result = body.get("data", {}).get("createPost", {})
    if result.get("__typename") != "PostActionSuccess":
        raise RuntimeError(f"Buffer publish to {platform} failed: {result.get('message', body)}")
    return result["post"]["id"]


# backwards-compatible alias
def publish_youtube(video_url: str, title: str, description: str) -> str:
    return buffer_post(video_url, description, config.BUFFER_YOUTUBE_CHANNEL_ID,
                       "youtube", title=title)


def publish(script, meta: dict, repo_root: Path) -> dict:
    out = {"hosted_url": None, "published": False,
           "youtube_post_id": None, "instagram_post_id": None, "tiktok_post_id": None}
    url = host_in_repo(script.out_path, script.slug, repo_root)
    out["hosted_url"] = url
    print(f"[publish] hosted: {url}")

    if os.environ.get("STICKFIN_AUTOPUBLISH") != "1":
        print("[publish] STICKFIN_AUTOPUBLISH != 1 -- hosted only, not posting")
        return out

    title = meta.get("title") or script.title
    desc = meta.get("description") or script.title

    # YouTube first (the important one); IG + TikTok are best-effort after.
    out["youtube_post_id"] = buffer_post(
        url, desc, config.BUFFER_YOUTUBE_CHANNEL_ID, "youtube", title=title)
    out["published"] = True
    print(f"[publish] YouTube post id: {out['youtube_post_id']}")

    for platform, channel in (("instagram", config.BUFFER_INSTAGRAM_CHANNEL_ID),
                              ("tiktok", config.BUFFER_TIKTOK_CHANNEL_ID)):
        if not channel:
            print(f"[publish] no {platform} channel configured -- skipping")
            continue
        try:
            pid = buffer_post(url, desc, channel, platform)
            out[f"{platform}_post_id"] = pid
            print(f"[publish] {platform} post id: {pid}")
        except Exception as e:  # non-fatal: YouTube already went out
            print(f"[publish] {platform} post FAILED (non-fatal): {e}")

    return out


# ---------------------------------------------------------------------------
# Batch mode: build now, post later. enqueue() hosts a finished short and
# records it in state/queue.json without posting anywhere; a separate,
# lightweight poster (tools/poster.py) later pulls one entry at a time and
# does ONLY the Buffer calls -- no GCP/Vertex call in that path at all, so
# the posting cadence can't be broken by trial-credit expiry or quota once
# the queue is full.
# ---------------------------------------------------------------------------

def _load_queue(repo_root: Path) -> list[dict]:
    p = repo_root / QUEUE_PATH
    return json.loads(p.read_text()) if p.exists() else []


def _save_queue(repo_root: Path, queue: list[dict]) -> None:
    p = repo_root / QUEUE_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(queue, indent=2))


def enqueue(script, meta: dict, repo_root: Path) -> dict:
    """Host a finished short and append it to the queue. No social post."""
    url = host_in_repo(script.out_path, script.slug, repo_root)
    entry = {
        "slug": script.slug,
        "title": meta.get("title") or script.title,
        "description": meta.get("description") or script.title,
        "hosted_url": url,
        "queued_date": dt.date.today().isoformat(),
        "posted": False,
        "posted_date": None,
        "post_ids": None,
    }
    queue = _load_queue(repo_root)
    queue.append(entry)
    _save_queue(repo_root, queue)
    print(f"[publish] queued: {entry['slug']}")
    return entry


def post_next_queued(repo_root: Path) -> dict | None:
    """Post the oldest un-posted queue entry to every configured channel.
    Buffer-only -- no GCP call. Returns the updated entry, or None if the
    queue has nothing left to post."""
    queue = _load_queue(repo_root)
    pending = [e for e in queue if not e["posted"]]
    if not pending:
        return None
    entry = pending[0]

    post_ids = {"youtube": None, "instagram": None, "tiktok": None}
    post_ids["youtube"] = buffer_post(
        entry["hosted_url"], entry["description"],
        config.BUFFER_YOUTUBE_CHANNEL_ID, "youtube", title=entry["title"])

    for platform, channel in (("instagram", config.BUFFER_INSTAGRAM_CHANNEL_ID),
                              ("tiktok", config.BUFFER_TIKTOK_CHANNEL_ID)):
        if not channel:
            continue
        try:
            post_ids[platform] = buffer_post(
                entry["hosted_url"], entry["description"], channel, platform)
        except Exception as e:  # non-fatal: YouTube already went out
            print(f"[poster] {platform} post FAILED (non-fatal): {e}")

    entry["posted"] = True
    entry["posted_date"] = dt.date.today().isoformat()
    entry["post_ids"] = post_ids
    _save_queue(repo_root, queue)
    return entry
