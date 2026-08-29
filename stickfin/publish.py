"""Publish the finished short to YouTube.

Mirrors core-decor-automation's proven path: commit the mp4 into the repo so
it's served from raw.githubusercontent.com, then hand that URL to Buffer's
GraphQL API with mode=shareNow. Buffer is used instead of the YouTube Data API
because YouTube's OAuth verification is high-friction for an automated poster
(same call core-decor made).

Env:
    BUFFER_API_KEY
    BUFFER_YOUTUBE_CHANNEL_ID
    GITHUB_REPOSITORY   (owner/repo, set automatically in Actions)
    STICKFIN_AUTOPUBLISH=1  to actually post (otherwise host-only, dry run)
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

YT_CATEGORY_EDUCATION = "27"


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


def publish_youtube(video_url: str, title: str, description: str) -> str:
    import requests

    token = os.environ["BUFFER_API_KEY"]
    channel = os.environ["BUFFER_YOUTUBE_CHANNEL_ID"]
    mutation = """
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
    post_input = {
        "channelId": channel,
        "mode": "shareNow",
        "schedulingType": "automatic",
        "needsApproval": False,
        "text": description,
        "assets": [{"video": {"url": video_url, "metadata": {"title": title}}}],
        "metadata": {"youtube": {"title": title, "categoryId": YT_CATEGORY_EDUCATION,
                                 "privacy": "public"}},
    }
    r = requests.post(
        "https://api.buffer.com/graphql",
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"query": mutation, "variables": {"input": post_input}}, timeout=60)
    result = r.json().get("data", {}).get("createPost", {})
    if result.get("__typename") != "PostActionSuccess":
        raise RuntimeError(f"Buffer publish failed: {result.get('message', r.json())}")
    return result["post"]["id"]


def publish(script, meta: dict, repo_root: Path) -> dict:
    out = {"hosted_url": None, "post_id": None, "published": False}
    url = host_in_repo(script.out_path, script.slug, repo_root)
    out["hosted_url"] = url
    print(f"[publish] hosted: {url}")

    if os.environ.get("STICKFIN_AUTOPUBLISH") != "1":
        print("[publish] STICKFIN_AUTOPUBLISH != 1 -- hosted only, not posting")
        return out

    desc = meta.get("description") or script.title
    out["post_id"] = publish_youtube(url, meta.get("title") or script.title, desc)
    out["published"] = True
    print(f"[publish] YouTube post id: {out['post_id']}")
    return out
