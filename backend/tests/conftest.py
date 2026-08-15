"""Pytest fixtures for the backend suite.

Test isolation: the whole suite runs against the **test** database
(``TEST_DATABASE_URL``, defaulting to ``DATABASE_URL``), never the app
database.  ``api.deps`` builds its engine at import time from
``DATABASE_URL``, so this module overrides that variable **before** any test
module imports it (conftest is imported by pytest before collection).  A
session-scoped autouse fixture then reseeds the demo dataset once per run,
so each test can rely on the canonical 60-person demo state.
"""

from __future__ import annotations

import os

from db.config import load_dotenv

load_dotenv()

_test_url = os.environ.get("TEST_DATABASE_URL") or os.environ.get("DATABASE_URL")
if _test_url:
    os.environ["DATABASE_URL"] = _test_url


import pytest
from api.deps import _SessionLocal


@pytest.fixture(scope="session", autouse=True)
def seeded_demo_db():
    """Reseed the demo dataset once per test session (idempotent, ~seconds).

    ``seed_demo`` always starts from a clean slate (delete-all + sequence
    reset), so no flags are needed.
    """
    from scripts.db.seed_demo import main as seed_demo_main

    seed_demo_main()
    yield


@pytest.fixture
def session():
    with _SessionLocal() as s:
        yield s
