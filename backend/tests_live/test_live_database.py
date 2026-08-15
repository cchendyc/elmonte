"""Read-only integration tests against the real application database.

Run explicitly with:

    python3 -m pytest backend/tests_live -q
"""

from __future__ import annotations

from datetime import date

from api.graphql.resolvers.explore import resolve_expand
from api.graphql.resolvers.person import resolve_person, resolve_person_export
from api.graphql.resolvers.perspective import resolve_perspective
from api.graphql.resolvers.projection import (
    resolve_person_coauthor_ties,
    resolve_projection,
)
from api.graphql.resolvers.search import resolve_search
from api.graphql.resolvers.universities import (
    resolve_org,
    resolve_org_children,
    resolve_universities,
)
from api.id_codec import decode, encode
from api.repositories.people import _person_brief, _person_briefs
from sqlalchemy import text


class FakeInfo:
    def __init__(self, db):
        self.context = {"db": db}


def _first_person_id(session) -> int:
    return int(session.execute(text("SELECT id FROM people ORDER BY id LIMIT 1")).scalar_one())


def _first_org_id(session) -> int:
    return int(
        session.execute(
            text(
                "SELECT id FROM organizations "
                "WHERE is_context_only = FALSE ORDER BY id LIMIT 1"
            )
        ).scalar_one()
    )


def test_live_database_has_core_data(session):
    people = int(session.execute(text("SELECT count(*) FROM people")).scalar_one())
    pubs = int(session.execute(text("SELECT count(*) FROM publications")).scalar_one())
    active = session.execute(
        text(
            "SELECT point_count FROM embedding_runs "
            "WHERE is_active ORDER BY id DESC LIMIT 1"
        )
    ).scalar_one()
    assert people > 0
    assert pubs > 0
    assert active is None or int(active) >= 0


def test_live_projection_is_complete_and_finite(session):
    info = FakeInfo(session)
    for view in ("topic", "network"):
        out = resolve_projection(None, info, view=view)
        assert out["runId"]
        assert out["view"] == view
        assert out["pointCount"] == len(out["points"]) > 0
        assert all(p["id"].startswith("p:") for p in out["points"])
        assert all(p["impact"] >= 0 for p in out["points"])
        assert all(c["memberCount"] > 0 for c in out["clusters"])
        assert all(e["sourceCluster"] != e["targetCluster"] for e in out["edges"])


def test_live_person_paths_and_batch_briefs_agree(session):
    info = FakeInfo(session)
    pid = _first_person_id(session)
    public_id = encode("person", pid)
    profile = resolve_person(None, info, id=public_id, on=date.today())
    assert profile is not None
    assert profile["id"] == public_id
    assert profile["label"]

    export = resolve_person_export(None, info, personId=public_id)
    assert export["id"] == public_id
    assert "aliases" in export
    assert "publications" in export

    perspective = resolve_perspective(None, info, personId=public_id)
    assert perspective["focusId"] == public_id
    assert perspective["alterCount"] == len(perspective["alters"])

    single = _person_brief(session, pid, date.today())
    batched = _person_briefs(session, [pid], date.today())
    assert single is not None
    assert batched[pid]["institution"] == single["institution"]
    assert batched[pid]["role"] == single["role"]


def test_live_expand_and_pages_for_first_org(session):
    info = FakeInfo(session)
    org_id = _first_org_id(session)
    public_id = encode("org", org_id)
    expanded = resolve_expand(None, info, id=public_id)
    assert expanded["focusId"] == public_id
    assert all(n["id"].startswith(("p:", "o:")) for n in expanded["nodes"])
    if expanded.get("page"):
        page = expanded["page"]
        assert page["total"] >= len(page["items"])
        assert page["ownerId"] == public_id


def test_live_search_finds_an_anchored_featured_person(session):
    """The live search path is verified against a person selected at runtime
    from the real DB — no researcher names are hardcoded in the repository."""
    info = FakeInfo(session)
    row = session.execute(
        text(
            """
            SELECT
              p.id,
              p.firstname,
              p.lastname,
              orcid.external_id AS orcid,
              root.name AS institution
            FROM people p
            JOIN person_anchor anchor
              ON anchor.person_id = p.id
             AND anchor.validity @> CURRENT_DATE
             AND anchor.is_primary
            JOIN org_tree_current tree ON tree.organization_id = anchor.organization_id
            JOIN organizations root ON root.id = tree.root_id AND root.kind = 'university'
            JOIN external_identifiers orcid
              ON orcid.person_id = p.id AND orcid.provider = 'orcid'
            WHERE EXISTS (
              SELECT 1 FROM publication_authors pa WHERE pa.person_id = p.id
            )
            ORDER BY p.id
            LIMIT 1
            """
        )
    ).mappings().first()
    assert row is not None, "live DB must contain an anchored person with ORCID and works"

    out = resolve_search(None, info, q=row["lastname"], limit=10)
    assert out["people"]
    hit = next(
        (h for h in out["people"] if h["id"] == encode("person", int(row["id"]))),
        None,
    )
    assert hit is not None
    assert hit["institution"] == row["institution"]
    assert hit["orcid"] == row["orcid"]
    assert hit["publicationCount"] > 0

    short_query = (row["lastname"] or row["firstname"])[:2]
    assert len(short_query) == 2
    short = resolve_search(None, info, q=short_query, limit=10)
    assert short["people"], "two-character search must not be empty on the live DB"


def test_live_universities_orgs_and_children(session):
    info = FakeInfo(session)
    universities = resolve_universities(None, info)
    assert universities, "the live database should expose at least one university"
    for unit in universities:
        assert unit["id"].startswith("o:")
        assert unit["orgKind"] == "university"

    unit = next((u for u in universities if u["childCount"]), universities[0])
    children = resolve_org_children(None, info, parentId=unit["id"])
    if unit["childCount"]:
        assert children
    profile = resolve_org(None, info, id=unit["id"])
    assert profile is not None
    assert profile["subtreePeopleCount"] >= profile["rosterCount"]


def test_live_coauthor_ties_are_deduplicated(session):
    info = FakeInfo(session)
    pid = int(
        session.execute(
            text(
                "SELECT person_id FROM publication_authors "
                "GROUP BY person_id ORDER BY count(*) DESC LIMIT 1"
            )
        ).scalar_one()
    )
    ties = resolve_person_coauthor_ties(
        None, info, personId=encode("person", pid), view="topic"
    )
    ids = [tie["personId"] for tie in ties]
    assert len(ids) == len(set(ids))


def test_live_id_round_trip_for_existing_rows(session):
    for table, kind in (("people", "person"), ("organizations", "org")):
        row_id = int(
            session.execute(text(f"SELECT id FROM {table} ORDER BY id LIMIT 1")).scalar_one()
        )
        encoded = encode(kind, row_id)
        decoded_kind, decoded_id = decode(encoded)
        assert decoded_kind == kind
        assert decoded_id == row_id
