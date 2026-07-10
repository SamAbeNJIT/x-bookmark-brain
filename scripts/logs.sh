#!/usr/bin/env bash
# Tail the live app logs from CloudWatch (App Runner ships stdout there).
#   scripts/logs.sh                  # follow everything
#   scripts/logs.sh sync.error       # only sync failures
#   scripts/logs.sh "tenant=<uuid>"  # one user's story
set -euo pipefail
GROUP="/aws/apprunner/xbb-app/a2d2a05817fb40f5ba44e4d28c9be4cf/application"
FILTER="${1:-}"
if [ -n "$FILTER" ]; then
  aws logs tail "$GROUP" --region us-east-1 --follow --since 1h --filter-pattern "\"$FILTER\""
else
  aws logs tail "$GROUP" --region us-east-1 --follow --since 1h
fi
