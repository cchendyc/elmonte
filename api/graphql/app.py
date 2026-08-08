"""Executable schema + FastAPI endpoint plumbing.

Ariadne provides two things we use here:

  * `make_executable_schema` — merges the SDL from `schema.graphql` with the
    resolver bindings from `resolvers.py`.
  * `graphql_sync` — the request-handling entry point. We call it from a
    plain FastAPI POST endpoint so we can keep using `Depends(db_session)`
    for per-request session lifecycle — no ASGI mount, no custom middleware.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ariadne import graphql_sync, load_schema_from_path, make_executable_schema
from ariadne.explorer import ExplorerGraphiQL
from sqlalchemy.orm import Session

from api.graphql.resolvers import date_scalar, query


_SCHEMA_PATH = Path(__file__).parent / "schema.graphql"

schema = make_executable_schema(
    load_schema_from_path(str(_SCHEMA_PATH)),
    query,
    date_scalar,
)


_EXPLORER = ExplorerGraphiQL()


def render_explorer() -> str:
    """GraphiQL HTML for the GET handler."""
    return _EXPLORER.html(None)


def execute(request_data: dict[str, Any], db: Session) -> tuple[bool, dict[str, Any]]:
    """Synchronously execute a parsed GraphQL request against the schema."""
    return graphql_sync(
        schema,
        request_data,
        context_value={"db": db},
        debug=True,
    )
