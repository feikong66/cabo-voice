#!/usr/bin/env bash
# One-shot deploy of the CABO all-in-one service (scoreboard + RVC voice) to
# Cloud Run. Safe to re-run: every step is idempotent.
#
#   bash deploy-cloudrun.sh
#
# Works in Google Cloud Shell, macOS/Linux, and Git Bash on Windows. The only
# prerequisites are the gcloud CLI and a project with billing enabled (billing
# must be on even while you stay inside the free tier).
#
# Override anything via the environment, e.g.
#   REGION=asia-northeast1 MODEL_ALLOW='nanami*' bash deploy-cloudrun.sh
set -euo pipefail

SERVICE="${SERVICE:-cabo}"
REGION="${REGION:-asia-east1}"
REPO="${REPO:-cabo}"
MODEL_ALLOW="${MODEL_ALLOW:-*}"
INCLUDE_INDEX="${INCLUDE_INDEX:-0}"
MEMORY="${MEMORY:-2Gi}"
CPU="${CPU:-2}"

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

command -v gcloud >/dev/null 2>&1 \
  || die "gcloud not found. Install the Google Cloud CLI, or run this script in Cloud Shell (https://shell.cloud.google.com)."

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
[ -n "$PROJECT" ] && [ "$PROJECT" != "(unset)" ] \
  || die "No project selected. Run: gcloud config set project YOUR_PROJECT_ID"

say "Project=$PROJECT  Service=$SERVICE  Region=$REGION  Memory=$MEMORY  CPU=$CPU"

say "Enabling required APIs (no-op if already on)"
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  --project "$PROJECT"

say "Ensuring Artifact Registry repository '$REPO' exists"
if ! gcloud artifacts repositories describe "$REPO" \
      --location "$REGION" --project "$PROJECT" >/dev/null 2>&1; then
  gcloud artifacts repositories create "$REPO" \
    --repository-format=docker \
    --location "$REGION" \
    --description "CABO all-in-one images" \
    --project "$PROJECT"
else
  echo "already exists"
fi

say "Building and deploying — expect 15-25 minutes on the first run"
echo "(installing CPU torch + downloading ~550MB of voice weights)"
gcloud builds submit \
  --config cloudbuild.yaml \
  --project "$PROJECT" \
  --substitutions "_SERVICE=$SERVICE,_REGION=$REGION,_REPO=$REPO,_MODEL_ALLOW=$MODEL_ALLOW,_INCLUDE_INDEX=$INCLUDE_INDEX,_MEMORY=$MEMORY,_CPU=$CPU" \
  .

URL="$(gcloud run services describe "$SERVICE" \
        --region "$REGION" --project "$PROJECT" \
        --format 'value(status.url)')"

say "Deployed: $URL"
cat <<EOF

Open that URL on your phone — the page detects the voice service on its own
origin, so there is nothing to configure. Add it to the home screen and it
behaves like an app.

Verify the voice engine actually came up (this is the step that catches a bad
fairseq install, which otherwise shows up only as "the phone used its own
voice"):

  curl -s $URL/healthz

Look at the "engine" object in the response:
  engine_loaded=true                -> RVC is live
  engine_error is a non-null string -> RVC is broken, that string says why

The first request after an idle period takes 30-60s while the instance cold
starts. To keep it warm (this costs money, it leaves the free tier):

  gcloud run services update $SERVICE --region $REGION \\
    --min-instances=1 --no-cpu-throttling
EOF
