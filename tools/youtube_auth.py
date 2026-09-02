"""One-time: get a long-lived YouTube refresh token for the upload workflow.

Run this ONCE, locally, signed in as the Google account that owns the
"Anti Broke" YouTube channel. It opens a browser, you approve, and it prints
the three values to store as GitHub Actions secrets.

    python tools/youtube_auth.py path/to/client_secret.json

Why a user OAuth token and not the service account the rest of the pipeline
uses: YouTube will not let a service account upload to a human-owned channel.
videos.insert requires user credentials, full stop.

IMPORTANT -- set the OAuth consent screen's publishing status to "In
production" BEFORE running this. While it is "Testing", Google expires every
refresh token after 7 days, which would break the workflow every week with an
invalid_grant error. In production the token lasts until it is revoked (you
will see an "unverified app" warning at consent; that is expected for a
personal app and is safe to continue past).
"""
from __future__ import annotations

import sys
from pathlib import Path

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def main() -> int:
    if len(sys.argv) < 2:
        raise SystemExit(__doc__)
    secret = Path(sys.argv[1])
    if not secret.exists():
        raise SystemExit(f"no such client secret file: {secret}")

    from google_auth_oauthlib.flow import InstalledAppFlow

    flow = InstalledAppFlow.from_client_secrets_file(str(secret), SCOPES)
    # access_type=offline + prompt=consent is what actually guarantees a
    # refresh token comes back (Google omits it on a repeat consent otherwise)
    creds = flow.run_local_server(port=0, access_type="offline", prompt="consent")

    if not creds.refresh_token:
        raise SystemExit("no refresh token returned -- revoke the app's access at "
                         "https://myaccount.google.com/permissions and re-run")

    print("\n" + "=" * 68)
    print("Store these as GitHub Actions secrets (Settings > Secrets > Actions):")
    print("=" * 68)
    print(f"YOUTUBE_CLIENT_ID       {creds.client_id}")
    print(f"YOUTUBE_CLIENT_SECRET   {creds.client_secret}")
    print(f"YOUTUBE_REFRESH_TOKEN   {creds.refresh_token}")
    print("=" * 68)
    print("Do NOT commit these. Nothing here writes them to disk.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
