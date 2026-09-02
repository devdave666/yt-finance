# Going hands-off — one-time setup

The pipeline code is done. To make the daily cron run unattended you need a
GitHub repo, two IAM tweaks on your GCP project, and one Buffer channel. After
that, `themes.yaml` is the only file you ever touch.

## 1. Repo

```bash
cd "C:/Users/Dev/Desktop/projects/YT finance"
git init && git add -A && git commit -m "stickfin pipeline"
gh repo create devdave666/yt-finance --private --source=. --push
```

## 2. GCP — extend the existing Workload Identity setup (your gcloud, IAM-admin)

The `github-pool` provider and `vertex-ai-runner` service account already exist
(from core-decor-automation). They're scoped to that one repo; two commands add
this repo. **Run these yourself** — they change IAM on your project and need
your own credentials by design.

```bash
PROJECT_ID="project-58f4f689-36b9-406b-bfa"
PROJECT_NUMBER="584573644858"

# a) let the new repo impersonate the runner service account
gcloud iam service-accounts add-iam-policy-binding \
  "vertex-ai-runner@${PROJECT_ID}.iam.gserviceaccount.com" \
  --project="$PROJECT_ID" --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/github-pool/attribute.repository/devdave666/yt-finance"

# b) widen the provider's attribute-condition to allow both repos
gcloud iam workload-identity-pools providers update-oidc "github-provider" \
  --project="$PROJECT_ID" --location="global" --workload-identity-pool="github-pool" \
  --attribute-condition="assertion.repository in ['devdave666/core-decor-automation','devdave666/yt-finance']"
```

Also grant the runner the two API scopes this pipeline needs (image gen was
already granted; TTS is new):

```bash
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:vertex-ai-runner@${PROJECT_ID}.iam.gserviceaccount.com" \
  --role="roles/serviceusage.serviceUsageConsumer"
gcloud services enable texttospeech.googleapis.com --project "$PROJECT_ID"
```

## 3. Buffer (YouTube + Instagram)

The "Anti Broke" YouTube channel and the `@antibrokee` Instagram account are
both connected on one Buffer account. Their channel ids are **not secret** and
are baked into `stickfin/config.py` (`BUFFER_*_CHANNEL_ID`), so the daily cron
posts to both with no extra setup. The only secret you need is:

- `BUFFER_API_KEY` — the Buffer personal (public API) token.

Overrides, if a channel ever changes: set `BUFFER_YOUTUBE_CHANNEL_ID` /
`BUFFER_INSTAGRAM_CHANNEL_ID` as env vars or Actions secrets. List the current
ids any time with `python tools/backfill_instagram.py --list` (needs
`BUFFER_API_KEY`). Instagram video posts go out as Reels
(`shouldShareToFeed: true`); the account must be Business/Creator for Buffer to
publish via API.

To push an already-built short to Instagram after the fact, run the **Backfill
Instagram** workflow with the slug, or `python tools/backfill_instagram.py
<slug>` locally.

## 3b. YouTube Data API — long-form only (one-time, needs you)

Buffer **cannot** upload long-form. Its YouTube integration on this channel is
Shorts-only and rejects anything over 3 minutes or non-vertical, and its
`YoutubePostMetadataInput` has no post-type field to override that (verified by
schema introspection, 2026-09-02). So 16:9 long-form goes through YouTube's own
API. Shorts keep using Buffer — don't move them, the API quota won't take it
(`videos.insert` costs 1600 of 10,000 units/day, i.e. ~6 uploads/day).

YouTube refuses service-account uploads to a human-owned channel, so this needs
a user OAuth token. Already done for you: the **YouTube Data API v3 is enabled**
on `project-58f4f689-36b9-406b-bfa`. The rest needs your Google account:

1. Console → **APIs & Services → OAuth consent screen**, User type **External**.
2. **Set Publishing status to "In production".** This matters: while it is
   "Testing", Google expires every refresh token after **7 days**, so the
   workflow would die with `invalid_grant` every week. In production the token
   lasts until revoked. You will see an "unverified app" warning at consent —
   expected for a personal app, continue past it.
3. **Credentials → Create credentials → OAuth client ID → Desktop app**.
   Download the JSON.
4. Signed in as the account that owns the "Anti Broke" channel, run once:
   ```bash
   pip install google-api-python-client google-auth-oauthlib
   python tools/youtube_auth.py path/to/client_secret.json
   ```
   It opens a browser, you approve, and it prints three values. Nothing is
   written to disk.
5. Repo → Settings → Secrets and variables → Actions, add all three:
   `YOUTUBE_CLIENT_ID`, `YOUTUBE_CLIENT_SECRET`, `YOUTUBE_REFRESH_TOKEN`.

Then upload with the **Upload Long-form** workflow (slug, title, description,
privacy — defaults to `unlisted`), or locally:

```bash
python tools/youtube_upload.py build/<slug>/<slug>.mp4 \
  --title "..." --privacy unlisted
```

There is a `--synthetic` flag that sets YouTube's altered/synthetic content
disclosure. It is **off by default and is your call**: the visuals are cartoon
stick figures (not the realistic synthetic media the policy targets) but the
narration is an AI voice.

## 4. First runs

```bash
# manual, build only, nothing posted:
gh workflow run daily.yml

# manual, actually post one:
gh workflow run daily.yml -f autopublish=true
```

The scheduled 15:00 UTC run auto-publishes. Change the time / frequency in
`.github/workflows/daily.yml`. Flip publishing off entirely by removing
`github.event_name == 'schedule'` from the `STICKFIN_AUTOPUBLISH` line.

## Steering the channel afterwards

- **Topics**: edit `themes.yaml` (`topics:` list, `cooldown:`, `niche:`, `rules:`).
- **Character look / voices**: `HOST_LOOK`, `SECOND_LOOK`, `CHANNEL_VOICE`,
  `SECOND_VOICE` in `stickfin/generate.py`.
- **Cut pace**: `STICKFIN_MAX_HOLD_S` (default 1.8s).
- **History**: `state/topic_history.json` (committed each run; delete a line to
  let a topic come back sooner).
