#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

load_env() {
  if [[ -f "$ROOT/.env" ]]; then
    set -a
    # shellcheck disable=SC1091
    source "$ROOT/.env"
    set +a
  fi

  if [[ -z "${DATABASE_URL:-}" ]]; then
    echo "Missing DATABASE_URL."
    echo "1. Copy .env.example to .env"
    echo "2. Paste your Neon connection string from https://console.neon.tech"
    exit 1
  fi
}
