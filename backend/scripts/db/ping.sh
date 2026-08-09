#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# shellcheck disable=SC1091
source "$SCRIPT_DIR/lib.sh"
load_env

psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -c "SELECT current_database() AS database, version();"
