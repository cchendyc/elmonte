#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib.sh"
load_env

# Always use Alembic for the schema: it stamps alembic_version as well as
# creating tables, so later `npm run db:migrate` calls keep working.  The
# initial migration executes db/schema.sql through SQLAlchemy (no psql client
# required); later historical migrations are guarded no-ops on a fresh DB.
echo "Applying schema (alembic upgrade head)..."
(
  cd "$BACKEND_ROOT"
  python3 -m alembic upgrade head
)

if [[ "${SKIP_SEED:-0}" != "1" ]]; then
  echo "Restoring the legacy demo seed..."
  (
    cd "$BACKEND_ROOT"
    python3 -m scripts.db.restore_legacy_seed
  )
fi

echo "Done. Tables:"
if command -v psql >/dev/null 2>&1; then
  psql "$DATABASE_URL" -c "\dt"
fi
