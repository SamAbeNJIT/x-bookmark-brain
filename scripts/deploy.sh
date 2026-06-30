#!/usr/bin/env bash
# Build → push → deploy the web app image to App Runner.
# Requires: Docker daemon (e.g. `colima start`) + aws CLI configured for the account.
# Usage:  bash scripts/deploy.sh
set -euo pipefail

REGION="us-east-1"
ECR="254335594676.dkr.ecr.us-east-1.amazonaws.com/xbb"
SERVICE_ARN="arn:aws:apprunner:us-east-1:254335594676:service/xbb-app/a2d2a05817fb40f5ba44e4d28c9be4cf"

cd "$(dirname "$0")/.."

echo "→ ECR login"
aws ecr get-login-password --region "$REGION" \
  | docker login --username AWS --password-stdin "${ECR%/*}"

echo "→ build (linux/amd64 — App Runner is x86_64; a default arm64 build will crash-loop)"
docker build --platform linux/amd64 -t xbb .
docker tag xbb:latest "$ECR:latest"

echo "→ push"
docker push "$ECR:latest"

echo "→ trigger App Runner deployment"
aws apprunner start-deployment --region "$REGION" --service-arn "$SERVICE_ARN" \
  --query OperationId --output text

echo "→ deployment started. Watch status with:"
echo "   aws apprunner describe-service --region $REGION --service-arn $SERVICE_ARN --query Service.Status --output text"
echo
echo "NOTE: schema changes are NOT applied by this script. If you changed storage.py's schema,"
echo "run the migration separately (init_db) BEFORE deploying, ideally with a lock_timeout so it"
echo "fails fast instead of blocking live traffic. App code-only changes need no migration."
