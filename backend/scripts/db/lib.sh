#!/usr/bin/env bash
set -euo pipefail

# backend/scripts/db/lib.sh — two roots after the monorepo split:
#   REPO_ROOT    = repo root (.env lives here, gitignored)
#   BACKEND_ROOT = backend/ (db/schema.sql, db/seed.sql live here)
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
BACKEND_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

load_env() {
  if [[ -f "$REPO_ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$REPO_ROOT/.env"
    set +a
  fi

  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "Missing DATABASE_URL."
    echo "1. Copy .env.example to .env"
    echo "2. Paste your Neon connection string from https://console.neon.tech"
    exit 1
  fi

  # psql / setup / migrations are session-level work: prefer the direct URL
  # (no PgBouncer pooler) when the project defines one.
  export DATABASE_URL="${DIRECT_URL:-$DATABASE_URL}"
}
