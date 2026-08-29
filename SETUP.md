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

## 3. Buffer channel for the new YouTube channel

1. Create the YouTube channel.
2. Connect it in Buffer (same Buffer account as core-decor is fine).
3. Get its channel id (Buffer URL, or the GraphQL `channels` query).
4. Repo → Settings → Secrets and variables → Actions:
   - `BUFFER_API_KEY` — your Buffer personal token (can be the same one)
   - `BUFFER_YOUTUBE_CHANNEL_ID` — the new channel's id

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
