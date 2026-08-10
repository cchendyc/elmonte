#!/usr/bin/env bash
set -euo pipefail

if [ -f api/main.py ]; then
  APP_DIR=.
elif [ -f backend/api/main.py ]; then
  APP_DIR=backend
else
  echo "Could not locate api.main (cwd=$(pwd))" >&2
  exit 1
fi

cd "$APP_DIR"
exec uvicorn api.main:app --host 0.0.0.0 --port "${PORT:?}"
