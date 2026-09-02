"""Upload a video straight to YouTube with the Data API (long-form capable).

Buffer cannot do this: its YouTube integration on this channel is Shorts-only
(<=3 min, vertical) and exposes no post-type field -- see tools/upload_longform.py.
So long-form goes through YouTube's own API, which also gives real control over
privacy, category and the synthetic-media disclosure.

    python tools/youtube_upload.py build/<slug>/<slug>.mp4 \
        --title "..." --description "..." --privacy unlisted

Env (all three from tools/youtube_auth.py):
    YOUTUBE_CLIENT_ID  YOUTUBE_CLIENT_SECRET  YOUTUBE_REFRESH_TOKEN

Quota: videos.insert costs 1600 of the default 10,000 units/day, i.e. about six
uploads a day. Plenty for long-form; do NOT route the 3x/day Shorts through it.
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
CATEGORY_EDUCATION = "27"


def _credentials():
    from google.oauth2.credentials import Credentials

    missing = [k for k in ("YOUTUBE_CLIENT_ID", "YOUTUBE_CLIENT_SECRET",
                           "YOUTUBE_REFRESH_TOKEN") if not os.environ.get(k)]
    if missing:
        raise SystemExit(
            f"missing {', '.join(missing)} -- run tools/youtube_auth.py once and "
            f"store the values as GitHub secrets (see SETUP.md)")
    return Credentials(
        None,
        refresh_token=os.environ["YOUTUBE_REFRESH_TOKEN"],
        client_id=os.environ["YOUTUBE_CLIENT_ID"],
        client_secret=os.environ["YOUTUBE_CLIENT_SECRET"],
        token_uri="https://oauth2.googleapis.com/token",
        scopes=SCOPES,
    )


def upload(path: Path, title: str, description: str, privacy: str,
           tags: list[str] | None = None, synthetic: bool = False,
           category: str = CATEGORY_EDUCATION) -> str:
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    body = {
        "snippet": {
            "title": title[:100],                 # YouTube hard-caps at 100
            "description": description[:5000],
            "tags": (tags or [])[:15],
            "categoryId": category,
        },
        "status": {
            "privacyStatus": privacy,
            "selfDeclaredMadeForKids": False,
        },
    }
    if synthetic:
        # YouTube's altered/synthetic content disclosure. Opt-in: a stick-figure
        # cartoon is not the "realistic" synthetic media the policy targets, but
        # the narration is an AI voice, so this is a judgement call worth making
        # explicitly rather than defaulting either way.
        body["status"]["containsSyntheticMedia"] = True

    youtube = build("youtube", "v3", credentials=_credentials(),
                    cache_discovery=False)
    media = MediaFileUpload(str(path), chunksize=8 * 1024 * 1024, resumable=True,
                            mimetype="video/mp4")
    request = youtube.videos().insert(
        part="snippet,status", body=body, media_body=media)

    print(f"[youtube] uploading {path.name} ({path.stat().st_size / 1e6:.1f} MB) "
          f"as {privacy}")
    response, last = None, -1
    while response is None:
        status, response = request.next_chunk()
        if status:
            pct = int(status.progress() * 100)
            if pct >= last + 10:
                print(f"[youtube]   {pct}%")
                last = pct
    vid = response["id"]
    print(f"[youtube] done: https://youtu.be/{vid}")
    print(f"[youtube] studio: https://studio.youtube.com/video/{vid}/edit")
    return vid


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("video")
    ap.add_argument("--title", required=True)
    ap.add_argument("--description", default="")
    ap.add_argument("--privacy", default="unlisted",
                    choices=["private", "unlisted", "public"])
    ap.add_argument("--tags", default="", help="comma separated")
    ap.add_argument("--synthetic", action="store_true",
                    help="declare altered/synthetic content")
    ap.add_argument("--category", default=CATEGORY_EDUCATION)
    args = ap.parse_args()

    path = Path(args.video)
    if not path.exists():
        raise SystemExit(f"no such file: {path}")

    upload(path, args.title, args.description, args.privacy,
           tags=[t.strip() for t in args.tags.split(",") if t.strip()],
           synthetic=args.synthetic, category=args.category)
    return 0


if __name__ == "__main__":
    sys.exit(main())
