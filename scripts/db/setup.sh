#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib.sh"
load_env

if [[ "${SKIP_SEED:-0}" == "1" ]]; then
  echo "Applying schema..."
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$ROOT/db/schema.sql"
else
  echo "Applying schema + seed..."
  (
    cd "$ROOT/db"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f seed.sql
  )
fi

echo "Done. Tables:"
psql "$DATABASE_URL" -c "\dt"
