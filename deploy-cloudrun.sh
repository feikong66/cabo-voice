#!/usr/bin/env bash
# One-shot deploy of the CABO all-in-one service (scoreboard + AI voice) to
# Cloud Run. Safe to re-run: every step is idempotent.
#
#   bash deploy-cloudrun.sh                  # tts mode, light and fast
#   VOICE_MODE=rvc bash deploy-cloudrun.sh   # character voices, heavy
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
VOICE_MODE="${VOICE_MODE:-tts}"
MODEL_ALLOW="${MODEL_ALLOW:-*}"
INCLUDE_INDEX="${INCLUDE_INDEX:-0}"

case "$VOICE_MODE" in
  tts)
    # Edge TTS proxy only: ~70MB RSS measured, requests are ~2s and mostly
    # spent waiting on Microsoft, so one CPU serves plenty of concurrency.
    MEMORY="${MEMORY:-512Mi}"; CPU="${CPU:-1}"; CONCURRENCY="${CONCURRENCY:-20}"
    EXTRA_ENV=""
    BUILD_NOTE="expect 2-3 minutes"
    ;;
  rvc)
    # torch + HuBERT + one voice model. Inference is serialised behind a lock,
    # so keep concurrency low or the queue outlives the request timeout.
    MEMORY="${MEMORY:-2Gi}"; CPU="${CPU:-2}"; CONCURRENCY="${CONCURRENCY:-4}"
    EXTRA_ENV=",RVC_F0_METHOD=${RVC_F0_METHOD:-rmvpe},RVC_WARMUP=1"
    BUILD_NOTE="expect 15-25 minutes on the first run (CPU torch + ~550MB of weights)"
    ;;
  *)
    echo "VOICE_MODE must be 'tts' or 'rvc', got '$VOICE_MODE'" >&2; exit 1 ;;
esac

say() { printf '\n\033[1;36m==> %s\033[0m\n' "$*"; }
die() { printf '\n\033[1;31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

command -v gcloud >/dev/null 2>&1 \
  || die "gcloud not found. Install the Google Cloud CLI, or run this script in Cloud Shell (https://shell.cloud.google.com)."

PROJECT="${PROJECT:-$(gcloud config get-value project 2>/dev/null || true)}"
[ -n "$PROJECT" ] && [ "$PROJECT" != "(unset)" ] \
  || die "No project selected. Run: gcloud config set project YOUR_PROJECT_ID"

say "Project=$PROJECT  Service=$SERVICE  Region=$REGION  Mode=$VOICE_MODE  Memory=$MEMORY  CPU=$CPU"

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

say "Building and deploying — $BUILD_NOTE"
gcloud builds submit \
  --config cloudbuild.yaml \
  --project "$PROJECT" \
  --substitutions "_SERVICE=$SERVICE,_REGION=$REGION,_REPO=$REPO,_VOICE_MODE=$VOICE_MODE,_MODEL_ALLOW=$MODEL_ALLOW,_INCLUDE_INDEX=$INCLUDE_INDEX,_MEMORY=$MEMORY,_CPU=$CPU,_CONCURRENCY=$CONCURRENCY,_EXTRA_ENV=$EXTRA_ENV" \
  .

URL="$(gcloud run services describe "$SERVICE" \
        --region "$REGION" --project "$PROJECT" \
        --format 'value(status.url)')"

say "Deployed: $URL"
cat <<EOF

Open that URL on your phone — the page detects the voice service on its own
origin, so there is nothing to configure. Add it to the home screen and it
behaves like an app.

Check what actually came up:

  curl -s $URL/healthz

  "mode":"$VOICE_MODE"        <- confirms which engine is live
  "voice_count":14          <- tts mode: how many voices the picker will show
  engine_error non-null     <- rvc mode only: RVC is broken, the string says why

That last one matters: when the engine breaks the service still serves the web
page and still passes the health check, so the only symptom is the phone
quietly falling back to its own voice.

Cold start after idle: a couple of seconds in tts mode, 30-60s in rvc mode
(loading HuBERT). To keep it warm — this costs money, it leaves the free tier:

  gcloud run services update $SERVICE --region $REGION \\
    --min-instances=1 --no-cpu-throttling
EOF
