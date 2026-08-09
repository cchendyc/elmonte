from __future__ import annotations

import os
from pathlib import Path


def load_dotenv() -> None:
    # backend/db/config.py -> parents[2] is the repo root (.env lives there).
    env_path = Path(__file__).resolve().parents[2] / ".env"
    if not env_path.exists():
        return

    for line in env_path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        value = value.strip().strip("'").strip('"')
        os.environ.setdefault(key.strip(), value)


def get_database_url() -> str:
    load_dotenv()
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set. Copy .env.example to .env and paste your Neon URL."
        )
    return url


def get_direct_url() -> str:
    """Return the Neon *direct* connection URL (no PgBouncer pooler).

    Use it for migrations, psql, and one-shot data pipelines — anything that
    holds a session or runs DDL.  Falls back to ``DATABASE_URL`` when the
    caller didn't configure ``DIRECT_URL`` (e.g. a local Postgres).
    """
    load_dotenv()
    return os.environ.get("DIRECT_URL") or get_database_url()
