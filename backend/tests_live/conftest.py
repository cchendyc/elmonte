"""Fixtures for the live-database suite.

These tests intentionally run against the real application database from
``.env`` (``DATABASE_URL``), never ``TEST_DATABASE_URL``. They are strictly
read-only and are excluded from the default pytest run so CI does not depend
on the production Neon instance.
"""

from __future__ import annotations

import os

from db.config import load_dotenv

load_dotenv()

# Deliberately do NOT point DATABASE_URL at TEST_DATABASE_URL here. This file
# is only imported by tests_live/.
assert os.environ.get("DATABASE_URL"), (
    "live suite requires the real DATABASE_URL from .env"
)

import pytest
from api.deps import _SessionLocal


@pytest.fixture
def session():
    with _SessionLocal() as s:
        yield s
