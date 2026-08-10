"""Endpoint-hardening tests for api/main.py and api/graphql/app.py.

These need no database: every case below is answered before any resolver
touches the session (security headers, body caps, payload validation, and
malformed-id handling are all pre-DB).
"""

import json

import pytest
from fastapi.testclient import TestClient

from api.graphql.app import execute
from api.main import app

client = TestClient(app)


# ---------------------------------------------------------------------------
# security headers (M1)
# ---------------------------------------------------------------------------


def test_security_headers_present():
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"
    assert r.headers.get("x-frame-options") == "DENY"
    assert r.headers.get("referrer-policy") == "no-referrer"
    assert "max-age=31536000" in r.headers.get("strict-transport-security", "")


def test_security_headers_on_privacy_page():
    r = client.get("/api/privacy")
    assert r.status_code == 200
    assert r.headers.get("x-content-type-options") == "nosniff"


# ---------------------------------------------------------------------------
# privacy endpoint (L2)
# ---------------------------------------------------------------------------


def test_privacy_cache_ttl_is_short():
    r = client.get("/api/privacy")
    assert r.status_code == 200
    assert "max-age=3600" in r.headers.get("cache-control", "")


# ---------------------------------------------------------------------------
# GraphQL body cap + payload validation (M3/M4)
# ---------------------------------------------------------------------------


def test_graphql_oversized_body_rejected():
    big = {"query": "query " + "{" + "a" * (70 * 1024) + "}"}
    r = client.post("/api/graphql", json=big)
    assert r.status_code == 413
    assert "large" in r.json()["detail"]


def test_graphql_non_object_body_rejected():
    r = client.post(
        "/api/graphql",
        content="[1, 2, 3]",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


def test_graphql_invalid_json_rejected():
    r = client.post(
        "/api/graphql",
        content="{not json",
        headers={"Content-Type": "application/json"},
    )
    assert r.status_code == 400


# ---------------------------------------------------------------------------
# CV endpoint malformed id (H3)
# ---------------------------------------------------------------------------


def test_cv_malformed_id_is_400_not_500():
    r = client.get("/api/people/not-an-id/cv")
    assert r.status_code == 400


def test_cv_org_id_is_404():
    r = client.get("/api/people/o:1234/cv")
    assert r.status_code == 404


# ---------------------------------------------------------------------------
# GraphQL execute() payload validation + depth guard (M3)
# ---------------------------------------------------------------------------


def test_execute_missing_query():
    ok, result = execute({}, None)
    assert ok is False
    assert result["errors"]


def test_execute_blank_query():
    ok, result = execute({"query": "   "}, None)
    assert ok is False


def test_execute_non_dict_payload():
    ok, result = execute(["query"], None)
    assert ok is False


def _nested_query(fields: int) -> str:
    """Balanced query nesting *fields* levels deep (plus a leaf)."""
    return (
        "query "
        + "{"
        + "".join(f" f{i} {{" for i in range(fields))
        + " x "
        + "}" * fields
        + "}"
    )


def test_execute_too_deep_rejected():
    ok, result = execute({"query": _nested_query(15)}, None)
    assert ok is False
    assert any("deep" in e["message"] for e in result["errors"])


def test_execute_max_depth_ok():
    # 11 nested fields + the leaf = max depth 12, which is exactly the limit.
    # The fields are unknown to the schema, so graphql_sync reports field
    # errors — the point is that the depth guard itself does NOT reject.
    ok, result = execute({"query": _nested_query(11)}, None)
    assert ok is False  # unknown-field errors from graphql-core
    assert all("deep" not in e["message"] for e in result["errors"])


def test_execute_syntax_error_is_clean_400():
    ok, result = execute({"query": "query {"}, None)
    assert ok is False
    assert result["errors"]


def test_execute_valid_query_ok():
    ok, result = execute({"query": "{ __typename }"}, None)
    assert ok is True
    assert result["data"]["__typename"] == "Query"


# ---------------------------------------------------------------------------
# GraphQL depth guard: named fragment chains (M3 bypass) (D-P1)
# ---------------------------------------------------------------------------


def test_query_depth_follows_named_fragment_chains():
    """A 30-deep fragment chain must exceed the depth limit, not hide behind
    a flat-looking document (the old walker skipped fragment_spread)."""
    from api.graphql.app import _query_depth

    chain = "\n".join(
        f"fragment F{i} on PersonProfile {{ label "
        + (f"...F{i + 1}" if i < 29 else "")
        + " }"
        for i in range(30)
    )
    query = f'query {{ person(id: "p:1") {{ ...F0 }} }} {chain}'
    assert _query_depth(query) > 12


def test_query_depth_cycle_guard_terminates():
    """Mutually-referencing fragments must not hang the walker."""
    from api.graphql.app import _query_depth

    query = (
        'query { person(id: "p:1") { ...F0 } } '
        "fragment F0 on PersonProfile { label ...F1 } "
        "fragment F1 on PersonProfile { label ...F0 } "
        "fragment F2 on PersonProfile { biography ...F2 } "
    )
    # Terminates; the cycle contributes 0 depth, acyclic parts count normally.
    assert _query_depth(query) > 0


def test_execute_rejects_deep_fragment_chain():
    """The full execute() path must refuse a chain beyond MAX_QUERY_DEPTH."""
    from api.graphql.app import execute

    chain = "\n".join(
        f"fragment F{i} on PersonProfile {{ label "
        + (f"...F{i + 1}" if i < 19 else "")
        + " }"
        for i in range(20)
    )
    ok, result = execute(
        {"query": f'query {{ person(id: "p:1") {{ ...F0 }} }} {chain}'}, None
    )
    assert not ok
    assert any("too deeply nested" in str(e.get("message", "")) for e in result.get("errors", []))
