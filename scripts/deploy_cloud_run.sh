#!/usr/bin/env bash
# Deploy the public demo to Google Cloud Run (personal project; never the work gcloud profile).
#
#   bash scripts/deploy_cloud_run.sh            # deploy or update (service stays private until --public)
#   bash scripts/deploy_cloud_run.sh --public   # also allow anyone to open it
#
# Needs: gcloud profile "docintel" logged in, billing linked to $PROJECT, GEMINI_API_KEY in .env.
# Cost guards: scales to zero, at most 2 instances, 1 vCPU / 1 GiB, demo caps (2 docs/visitor/day, 150/day total).
set -euo pipefail
cd "$(dirname "$0")/.."
export CLOUDSDK_ACTIVE_CONFIG_NAME=${GCLOUD_PROFILE:-docintel}
PROJECT=${PROJECT:-docintel-demo-sivaram}
REGION=${REGION:-us-central1}
SERVICE=docintel
set -a; . ./.env; set +a

echo "== services"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com --project "$PROJECT"

echo "== secrets (values never printed)"
for name in GEMINI_API_KEY DEMO_SALT; do
  if [ "$name" = DEMO_SALT ]; then value=$(python -c "import secrets;print(secrets.token_hex(16))"); else value=$GEMINI_API_KEY; fi
  if ! gcloud secrets describe "$name" --project "$PROJECT" >/dev/null 2>&1; then
    printf %s "$value" | gcloud secrets create "$name" --data-file=- --project "$PROJECT" >/dev/null
  elif [ "$name" = GEMINI_API_KEY ]; then
    printf %s "$value" | gcloud secrets versions add "$name" --data-file=- --project "$PROJECT" >/dev/null
  fi
done
NUM=$(gcloud projects describe "$PROJECT" --format="value(projectNumber)")
for name in GEMINI_API_KEY DEMO_SALT; do
  gcloud secrets add-iam-policy-binding "$name" --project "$PROJECT" --role roles/secretmanager.secretAccessor \
    --member "serviceAccount:${NUM}-compute@developer.gserviceaccount.com" >/dev/null
done

echo "== build + deploy (Cloud Build uses the repository Dockerfile)"
gcloud run deploy "$SERVICE" --source . --project "$PROJECT" --region "$REGION" \
  --port 8080 --cpu 1 --memory 1Gi --min-instances 0 --max-instances 2 --concurrency 20 --timeout 300 \
  --set-secrets "GEMINI_API_KEY=GEMINI_API_KEY:latest,DEMO_SALT=DEMO_SALT:latest" \
  --set-env-vars "DEMO_MODE=1,DEMO_MAX_DOCS=2,DEMO_MAX_PAGES=3,DEMO_DAILY_CAP=150,DEMO_TTL_HOURS=24,DEMO_LONGDOC_PAGES=15,DEMO_MAX_QUESTIONS=10,TRUST_PROXY_HOPS=1,EXTRACTOR=gemini:gemini-3.5-flash-lite,SUMMARY_MODEL=gemini:gemini-3.5-flash-lite,WORKERS=2,DEMO_DIR=/app/samples,FRAME_ANCESTORS='self'" \
  --no-allow-unauthenticated --quiet

if [ "${1:-}" = "--public" ]; then
  gcloud run services add-iam-policy-binding "$SERVICE" --project "$PROJECT" --region "$REGION" \
    --member allUsers --role roles/run.invoker >/dev/null
  echo "== public"
fi
gcloud run services describe "$SERVICE" --project "$PROJECT" --region "$REGION" --format "value(status.url)"
