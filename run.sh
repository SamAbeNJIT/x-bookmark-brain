#!/usr/bin/env bash
# Start the local web app. Loads .env (Bedrock model IDs + X cookies) and serves on :8000.
#   ./run.sh            -> start (Ctrl-C to stop)
#   ./run.sh --reload   -> start with auto-reload for development
# Data lives in data/xbb.db on disk and persists across restarts.
set -euo pipefail
cd "$(dirname "$0")"
source .venv/bin/activate
set -a; [ -f .env ] && . ./.env; set +a
echo "→ http://127.0.0.1:8000   (Ctrl-C to stop)"
exec uvicorn xbb.web:app --host 127.0.0.1 --port 8000 "$@"
