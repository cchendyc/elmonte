#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib.sh"
load_env

# schema.sql is a full rebuild for FRESH databases only — it does not know
# the migration-only tables (topics, projections, ...), so running it over a
# migrated database silently wipes their data.  On Neon (or any migrated
# DB) use `alembic upgrade head` instead.
applied="$(psql "$DATABASE_URL" -tAc "SELECT count(*) FROM alembic_version" 2>/dev/null || true)"
if [[ "$applied" =~ ^[0-9]+$ ]] && [[ "$applied" -gt 0 ]]; then
  echo "Refusing to run setup.sh: the database already has $applied applied"
  echo "migration(s). Use 'npm run db:migrate' (alembic upgrade head) instead."
  exit 1
fi

if [[ "${SKIP_SEED:-0}" == "1" ]]; then
  echo "Applying schema..."
  psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f "$BACKEND_ROOT/db/schema.sql"
else
  echo "Applying schema + seed..."
  (
    cd "$BACKEND_ROOT/db"
    psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -f seed.sql
  )
fi

echo "Done. Tables:"
psql "$DATABASE_URL" -c "\dt"
