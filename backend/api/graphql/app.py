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
# Width limits: GraphQL depth guards do not stop `{ a: projection b: projection
# ... }` alias amplification.  The frontend uses no aliases today, so these
# limits are generous for real clients and small for attackers.
MAX_QUERY_ALIASES = 24
MAX_QUERY_SELECTIONS = 400
MAX_QUERY_DEFINITIONS = 40
MAX_QUERY_OPERATIONS = 1


def _parse_query(query: str) -> Any:
    """Parse once and let the caller turn syntax errors into a clean 400."""
    return parse(query)  # raises GraphQLSyntaxError on bad syntax


def _fragment_map(document: Any) -> dict[str, Any]:
    return {
        definition.name.value: definition
        for definition in document.definitions
        if definition.kind == "fragment_definition"
    }


def _query_depth_from_document(document: Any) -> int:
    """Approximate maximum field-nesting depth of a GraphQL document.

    Counts one level per nested ``selection_set`` under operations *and*
    follows named fragment spreads (a client can otherwise chain fragments
    arbitrarily deep while the raw document looks flat).  Fragment depths are
    memoized and spread chains are guarded against cycles (graphql-core would
    reject them at validation, but this runs before validation).
    """
    fragments = _fragment_map(document)
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
        fragment = fragments.get(name)
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


def _query_width_error(document: Any) -> str | None:
    """Reject alias/selection-count amplification before graphql-core runs."""
    if len(document.definitions) > MAX_QUERY_DEFINITIONS:
        return f"too many definitions (max {MAX_QUERY_DEFINITIONS})"

    operations = [
        d for d in document.definitions if d.kind == "operation_definition"
    ]
    if len(operations) > MAX_QUERY_OPERATIONS:
        return f"too many operations (max {MAX_QUERY_OPERATIONS})"

    fragments = _fragment_map(document)
    aliases = 0

    def walk_selection_set(selection_set: Any, stack: set[str]) -> int | None:
        """Return effective selection count, or None when over budget.

        Fragment spreads are expanded inline for each spread site.  That is
        deliberately pessimistic for execution cost (a fragment reused at two
        call sites runs its fields twice) and matches how the resolver cache
        amplifications are measured.
        """
        nonlocal aliases
        count = 0
        if not selection_set:
            return 0
        for selection in selection_set.selections:
            if selection.kind == "field":
                count += 1
                if selection.alias is not None:
                    aliases += 1
                    if aliases > MAX_QUERY_ALIASES:
                        return None
                if selection.selection_set:
                    sub = walk_selection_set(selection.selection_set, stack)
                    if sub is None:
                        return None
                    count += sub
            elif selection.kind == "inline_fragment":
                sub = walk_selection_set(selection.selection_set, stack)
                if sub is None:
                    return None
                count += sub
            elif selection.kind == "fragment_spread":
                name = selection.name.value
                if name in stack:
                    continue  # cycle guard; validation reports the real error
                fragment = fragments.get(name)
                if fragment is not None:
                    sub = walk_selection_set(
                        fragment.selection_set, {*stack, name}
                    )
                    if sub is None:
                        return None
                    count += sub
            if count > MAX_QUERY_SELECTIONS:
                return None
        return count

    for operation in operations:
        count = walk_selection_set(operation.selection_set, set())
        if count is None:
            if aliases > MAX_QUERY_ALIASES:
                return f"too many aliases (max {MAX_QUERY_ALIASES})"
            return f"too many field selections (max {MAX_QUERY_SELECTIONS})"

    return None


def _query_depth(query: str) -> int:
    """Backward-compatible wrapper used by the depth-guard tests."""
    return _query_depth_from_document(_parse_query(query))


def execute(request_data: dict[str, Any], db: Session) -> tuple[bool, dict[str, Any]]:
    """Synchronously execute a parsed GraphQL request against the schema."""
    # Payload sanity + shape guards before graphql-core does any work.
    if not isinstance(request_data, dict):
        return False, {"errors": [{"message": "request body must be a JSON object"}]}
    query = request_data.get("query")
    if not isinstance(query, str) or not query.strip():
        return False, {"errors": [{"message": "missing GraphQL query"}]}
    try:
        document = _parse_query(query)
        depth = _query_depth_from_document(document)
    except GraphQLSyntaxError as exc:
        return False, {"errors": [{"message": str(exc)}]}
    if depth > MAX_QUERY_DEPTH:
        return False, {
            "errors": [
                {"message": f"query too deeply nested (max depth {MAX_QUERY_DEPTH})"}
            ]
        }
    width_error = _query_width_error(document)
    if width_error is not None:
        return False, {"errors": [{"message": width_error}]}
    return graphql_sync(
        schema,
        request_data,
        context_value={"db": db, "_resolver_cache": {}},
        debug=False,
    )
