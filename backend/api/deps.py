"""FastAPI dependencies: database session lifecycle."""

from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from db.config import get_database_url


def _normalise_url(url: str) -> str:
    """Force the psycopg (v3) dialect. Bare `postgresql://` picks the legacy
    psycopg2 driver that isn't installed; `postgresql+psycopg://` uses the
    v3 driver we do ship."""
    if url.startswith("postgresql://"):
        return "postgresql+psycopg://" + url[len("postgresql://") :]
    if url.startswith("postgres://"):
        return "postgresql+psycopg://" + url[len("postgres://") :]
    return url


# One engine per process. `pool_pre_ping` cheaply guards against Neon's idle-
# disconnect behaviour without full pool churn.
_engine = create_engine(
    _normalise_url(get_database_url()),
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=5,
    future=True,
)

_SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)


def db_session() -> Generator[Session, None, None]:
    """FastAPI dependency: yields a scoped session, closes on exit."""
    session = _SessionLocal()
    try:
        yield session
    finally:
        session.close()
