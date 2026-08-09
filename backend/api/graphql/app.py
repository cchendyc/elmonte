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
from graphql import GraphQLSyntaxError, parse
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


# Bounded by the frontend's own queries (deepest is the profile at ~6 fields);
# anything deeper is an attacker probing with nesting bombs (M3).
MAX_QUERY_DEPTH = 12


def _query_depth(query: str) -> int:
    """Approximate maximum field-nesting depth of a GraphQL document.

    Counts one level per nested ``selection_set`` under operations *and*
    follows named fragment spreads (a client can otherwise chain fragments
    arbitrarily deep while the raw document looks flat).  Fragment depths are
    memoized and spread chains are guarded against cycles (graphql-core would
    reject them at validation, but this runs before validation).
    """
    document = parse(query)  # raises GraphQLSyntaxError on bad syntax
    memo: dict[str, int] = {}

    def selection_depth(selection_set: Any, stack: set[str]) -> int:
        depth = 1
        if not selection_set:
            return depth
        for selection in selection_set.selections:
            if selection.kind == "field":
                if selection.selection_set:
                    depth = max(
                        depth, 1 + selection_depth(selection.selection_set, stack)
                    )
            elif selection.kind == "inline_fragment":
                depth = max(depth, selection_depth(selection.selection_set, stack))
            elif selection.kind == "fragment_spread":
                depth = max(depth, 1 + fragment_depth(selection.name.value, stack))
        return depth

    def fragment_depth(name: str, stack: set[str]) -> int:
        if name in memo:
            return memo[name]
        if name in stack:
            return 0  # cycle guard — terminate the walk
        fragment = next(
            (
                d
                for d in document.definitions
                if d.kind == "fragment_definition" and d.name.value == name
            ),
            None,
        )
        if fragment is None:
            return 0
        stack.add(name)
        depth = selection_depth(fragment.selection_set, stack)
        stack.discard(name)
        memo[name] = depth
        return depth

    max_depth = 0
    for definition in document.definitions:
        if definition.kind == "operation_definition":
            max_depth = max(
                max_depth, selection_depth(definition.selection_set, set())
            )
        elif definition.kind == "fragment_definition":
            max_depth = max(
                max_depth, fragment_depth(definition.name.value, set())
            )
    return max_depth


def execute(request_data: dict[str, Any], db: Session) -> tuple[bool, dict[str, Any]]:
    """Synchronously execute a parsed GraphQL request against the schema."""
    # Payload sanity + nesting-depth guard before graphql-core does any work.
    if not isinstance(request_data, dict):
        return False, {"errors": [{"message": "request body must be a JSON object"}]}
    query = request_data.get("query")
    if not isinstance(query, str) or not query.strip():
        return False, {"errors": [{"message": "missing GraphQL query"}]}
    try:
        depth = _query_depth(query)
    except GraphQLSyntaxError as exc:
        return False, {"errors": [{"message": str(exc)}]}
    if depth > MAX_QUERY_DEPTH:
        return False, {
            "errors": [
                {"message": f"query too deeply nested (max depth {MAX_QUERY_DEPTH})"}
            ]
        }
    return graphql_sync(
        schema,
        request_data,
        context_value={"db": db},
        debug=False,
    )
