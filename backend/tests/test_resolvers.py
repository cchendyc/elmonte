"""Integration tests for GraphQL resolvers against the seeded demo DB."""

from datetime import date

import pytest
from api.graphql.resolvers.person import resolve_person
from api.graphql.resolvers.perspective import resolve_perspective
from api.graphql.resolvers.projection import (
    resolve_person_coauthor_ties,
    resolve_projection,
)
from api.graphql.resolvers.search import resolve_search
from api.graphql.resolvers.universities import resolve_org, resolve_universities
from api.id_codec import decode, encode
from api.repositories.people import _person_brief, _person_briefs
from graphql import GraphQLError
from sqlalchemy import text


class FakeInfo:
    def __init__(self, db):
        self.context = {"db": db}


@pytest.mark.integration
def test_projection_topic_view(session):
    info = FakeInfo(session)
    out = resolve_projection(None, info, view="topic")
    assert out["view"] == "topic"
    assert out["pointCount"] == len(out["points"]) > 0
    assert len(out["clusters"]) > 0
    for p in out["points"]:
        assert p["clusterId"] is not None
        assert p["clusterLabel"]
    for c in out["clusters"]:
        assert c["label"] and c["memberCount"] > 0
    for e in out["edges"]:
        assert e["sourceCluster"] != e["targetCluster"]


@pytest.mark.integration
def test_projection_network_view(session):
    info = FakeInfo(session)
    out = resolve_projection(None, info, view="network")
    assert out["view"] == "network"
    assert out["pointCount"] == len(out["points"]) > 0


@pytest.mark.integration
def test_projection_invalid_view_defaults(session):
    info = FakeInfo(session)
    out = resolve_projection(None, info, view="bogus")
    assert out["view"] == "topic"


@pytest.mark.integration
def test_perspective_shape(session):
    info = FakeInfo(session)
    # p:5 is the demo hub with 16 alters.
    out = resolve_perspective(None, info, personId=encode("person", 5))
    assert out["focusId"] == encode("person", 5)
    assert out["alterCount"] == len(out["alters"]) > 0
    for a in out["alters"]:
        assert 0.0 <= a["importance"] <= 1.0
        assert a["hop"] in (1, 2)
        assert a["group"] >= 0


@pytest.mark.integration
def test_person_profile_shape(session):
    info = FakeInfo(session)
    out = resolve_person(None, info, id=encode("person", 1), on=date.today())
    assert out is not None
    assert out["label"]
    assert out["careerTimeline"] is not None
    assert "publications" in out


@pytest.mark.integration
def test_search_finds_demo_person(session):
    # The suite seeds the synthetic demo dataset (60 people named from the
    # FIRST_NAMES/LAST_NAMES pools — "Smith" is person 1, James Smith).
    info = FakeInfo(session)
    out = resolve_search(None, info, q="Smith", limit=5)
    assert len(out["people"]) > 0
    assert all(h["label"] for h in out["people"])


@pytest.mark.integration
def test_malformed_person_id_raises_graphql_error(session):
    """A malformed public id must be a field error, not a 500."""
    info = FakeInfo(session)
    with pytest.raises(GraphQLError):
        resolve_person(None, info, id="p:!!!")


@pytest.mark.integration
def test_kind_mismatch_raises_graphql_error(session):
    info = FakeInfo(session)
    with pytest.raises(GraphQLError):
        resolve_person(None, info, id=encode("org", 1))


@pytest.mark.integration
def test_active_projection_run_is_deterministic(session):
    """Projection and personCoauthorTies must agree on the same active run."""
    from api.repositories.projection import _active_projection_run

    active = _active_projection_run(session)
    assert active is not None
    out = resolve_projection(None, FakeInfo(session), view="topic")
    assert out["runId"] == str(active["id"])


@pytest.mark.integration
def test_coauthor_ties_have_no_duplicate_persons(session):
    """The projection join must filter by view — otherwise every coauthor
    comes back once per view and the frontend's per-person React keys
    collide (edges get dropped)."""
    info = FakeInfo(session)
    out = resolve_person_coauthor_ties(
        None, info, personId=encode("person", 1), view="topic"
    )
    ids = [t["personId"] for t in out]
    assert len(ids) == len(set(ids)), f"duplicate ties: {ids}"
    for t in out:
        assert t["paperCount"] > 0


@pytest.mark.integration
def test_person_briefs_batch_matches_singleton(session):
    """Batched _person_briefs must agree with the per-person _person_brief."""
    info = FakeInfo(session)
    on = date.today()
    out = resolve_perspective(None, info, personId=encode("person", 5))
    pids = [int(decode(a["personId"])[1]) for a in out["alters"]]
    assert pids, "perspective should return alters for the demo hub"
    batched = _person_briefs(session, pids, on)
    for pid in pids:
        single = _person_brief(session, pid, on)
        assert single is not None
        b = batched[pid]
        assert b is not None
        assert b["person"].id == single["person"].id
        assert b["institution"] == single["institution"]
        assert b["role"] == single["role"]
        assert b["rank"] == single["rank"]
        assert b["retiredAt"] == single["retiredAt"]


@pytest.mark.integration
def test_expand_person_shape(session):
    """expand builds an ego network with encoded ids and typed links."""
    from api.graphql.resolvers.explore import resolve_expand

    info = FakeInfo(session)
    out = resolve_expand(None, info, id=encode("person", 1))
    assert out["focusId"] == encode("person", 1)
    assert len(out["nodes"]) > 0
    assert len(out["links"]) > 0
    for node in out["nodes"]:
        assert node["id"].startswith(("p:", "o:"))
    for link in out["links"]:
        assert link["source"].startswith(("p:", "o:"))
        assert link["target"].startswith(("p:", "o:"))
        assert "relation" in link


@pytest.mark.integration
def test_universities_shape(session):
    """universities returns the demo orgs with labels and kinds."""
    from api.graphql.resolvers.universities import resolve_universities

    info = FakeInfo(session)
    out = resolve_universities(None, info)
    assert len(out) > 0
    for unit in out:
        assert unit["label"]
        assert unit["id"].startswith("o:")
        assert unit["orgKind"] == "university"


@pytest.mark.integration
def test_org_children_shape(session):
    """orgChildren nests one level under a university."""
    from api.graphql.resolvers.universities import (
        resolve_org_children,
        resolve_universities,
    )

    info = FakeInfo(session)
    # Find a university that actually has children in the demo seed (MIT and
    # UC Berkeley each have one unit) — a childless org returns [].
    unit = next(
        (
            u
            for u in resolve_universities(None, info)
            if resolve_org_children(None, info, parentId=u["id"])
        ),
        None,
    )
    assert unit is not None, "demo seed should give at least one university a child"
    children = resolve_org_children(None, info, parentId=unit["id"])
    assert children
    for child in children:
        assert child["id"].startswith("o:")
        assert child["label"]
        assert child["orgKind"]


@pytest.mark.integration
def test_person_export_shape(session):
    """personExport returns the full profile export with publications."""
    from api.graphql.resolvers.person import resolve_person_export

    info = FakeInfo(session)
    out = resolve_person_export(None, info, personId=encode("person", 1))
    assert out["id"] == encode("person", 1)
    assert out["label"]
    assert "careerTimeline" in out
    assert "publications" in out
    assert "personTopics" in out


# ---------------------------------------------------------------------------
# Added coverage: org profile, deterministic search, hop-2 perspective SQL
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_org_profile_shape_and_counts(session):
    info = FakeInfo(session)
    unit = next(
        (u for u in resolve_universities(None, info) if u["childCount"]),
        resolve_universities(None, info)[0],
    )
    out = resolve_org(None, info, id=unit["id"])
    assert out is not None
    assert out["id"] == unit["id"]
    assert out["label"]
    assert out["orgKind"] == "university"
    assert isinstance(out["children"], list)
    assert isinstance(out["externalIdentifiers"], list)
    assert out["rosterCount"] >= 0
    assert out["subtreePeopleCount"] >= out["rosterCount"]
    for child in out["children"]:
        assert child["childCount"] is not None
        assert child["rosterCount"] is not None


@pytest.mark.integration
def test_org_profile_missing_id_is_none(session):
    info = FakeInfo(session)
    assert resolve_org(None, info, id=encode("org", 999_999)) is None


@pytest.mark.integration
def test_subtree_count_is_as_of_aware(session):
    """The current-date matview is a fast path only; historical org profiles
    must count the temporal tree for the requested date, not today's."""
    from api.repositories.orgs import _subtree_people_count

    info = FakeInfo(session)
    unit = next(
        (u for u in resolve_universities(None, info) if u["childCount"]),
        resolve_universities(None, info)[0],
    )
    row_id = decode(unit["id"])[1]
    today = date.today()
    out = resolve_org(None, info, id=unit["id"], on=today)
    assert out is not None
    assert out["subtreePeopleCount"] == _subtree_people_count(session, row_id, today)
    # Open-ended (null validity) affiliations are active for every date by
    # design; the historical path must still run without error and return a
    # sane non-negative count.
    assert _subtree_people_count(session, row_id, date(1800, 1, 1)) >= 0


@pytest.mark.integration
def test_search_result_order_is_deterministic(session):
    info = FakeInfo(session)
    first = resolve_search(None, info, q="Smith", limit=20)
    second = resolve_search(None, info, q="Smith", limit=20)
    assert [p["id"] for p in first["people"]] == [
        p["id"] for p in second["people"]
    ]
    assert [o["id"] for o in first["orgs"]] == [
        o["id"] for o in second["orgs"]
    ]


@pytest.mark.integration
def test_search_short_query_uses_ilike_fallback(session):
    """Two-character queries must not return an empty panel just because
    pg_trgm's % operator is effectively useless below three characters."""
    info = FakeInfo(session)
    out = resolve_search(None, info, q="Sm", limit=5)
    assert out["people"], "short prefix search should fall back to ILIKE"


@pytest.mark.integration
def test_search_escapes_like_wildcards(session):
    """A user typing `%` or `_` is searching for a literal character, not
    asking for a wildcard query that matches the entire directory."""
    info = FakeInfo(session)
    for wildcard in ("%", "_"):
        out = resolve_search(None, info, q=wildcard, limit=50)
        assert out["people"] == []
        assert out["orgs"] == []


@pytest.mark.integration
def test_search_hit_has_disambiguation_fields(session):
    """Search cards need ORCID / research area / bibliometric context for
    disambiguation; the resolver must return every documented field."""
    info = FakeInfo(session)
    out = resolve_search(None, info, q="Smith", limit=5)
    assert out["people"]
    hit = out["people"][0]
    for key in (
        "orcid",
        "researchArea",
        "publicationCount",
        "lastPublicationYear",
    ):
        assert key in hit
    assert hit["publicationCount"] >= 0


@pytest.mark.integration
def test_perspective_returns_shared_coauthor_hop_two(session):
    """A person connected to two of my coauthors (but not to me) must appear
    as a hop-2 alter.  This is the SQL bug where the old query looked at edges
    among my direct coauthors instead of edges from an outside person to them.
    """
    focus, direct_a, direct_b, outside = 1001, 1002, 1003, 1004
    pub_focus_a, pub_focus_b = 20001, 20002
    pub_x_a, pub_x_b = 20003, 20004

    def insert_pub(pub_id, year, authors):
        session.execute(
            text(
                "INSERT INTO publications (id, title, publication_year) "
                "VALUES (:id, :title, :year)"
            ),
            {"id": pub_id, "title": f"Hop2 test paper {pub_id}", "year": year},
        )
        for position, author_id in enumerate(authors, start=1):
            session.execute(
                text(
                    "INSERT INTO publication_authors "
                    "(publication_id, person_id, author_position) "
                    "VALUES (:pub, :pid, :pos)"
                ),
                {"pub": pub_id, "pid": author_id, "pos": position},
            )

    def cleanup():
        for pub_id in (pub_focus_a, pub_focus_b, pub_x_a, pub_x_b):
            session.execute(
                text("DELETE FROM publication_authors WHERE publication_id = :id"),
                {"id": pub_id},
            )
            session.execute(
                text("DELETE FROM publications WHERE id = :id"), {"id": pub_id}
            )
        for person_id in (focus, direct_a, direct_b, outside):
            session.execute(
                text("DELETE FROM people WHERE id = :id"), {"id": person_id}
            )
        session.commit()
        session.execute(
            text("REFRESH MATERIALIZED VIEW CONCURRENTLY person_coauthor_edges")
        )
        session.commit()

    try:
        for person_id in (focus, direct_a, direct_b, outside):
            session.execute(
                text(
                    "INSERT INTO people (id, firstname, lastname, claimed_status) "
                    "VALUES (:id, :first, :last, 'unclaimed')"
                ),
                {"id": person_id, "first": f"Person {person_id}", "last": "Hop2"},
            )
        insert_pub(pub_focus_a, 2024, (focus, direct_a))
        insert_pub(pub_focus_b, 2024, (focus, direct_b))
        insert_pub(pub_x_a, 2024, (outside, direct_a))
        insert_pub(pub_x_b, 2024, (outside, direct_b))
        session.commit()
        session.execute(
            text("REFRESH MATERIALIZED VIEW CONCURRENTLY person_coauthor_edges")
        )
        session.commit()

        info = FakeInfo(session)
        out = resolve_perspective(
            None, info, personId=encode("person", focus)
        )
        by_id = {alter["personId"]: alter for alter in out["alters"]}
        assert encode("person", direct_a) in by_id
        assert encode("person", direct_b) in by_id
        hop2 = by_id[encode("person", outside)]
        assert hop2["hop"] == 2
        assert hop2["importance"] > 0
    finally:
        cleanup()


@pytest.mark.integration
def test_expand_respects_as_of_relationship_validity(session):
    """Expired advisor edges must not leak into an as-of-date expansion."""
    from api.graphql.resolvers.explore import resolve_expand

    advisor, advisee = 1101, 1102
    session.execute(
        text(
            "INSERT INTO people (id, firstname, lastname, claimed_status) "
            "VALUES (:id, :first, :last, 'unclaimed')"
        ),
        {"id": advisor, "first": "Old", "last": "Advisor"},
    )
    session.execute(
        text(
            "INSERT INTO people (id, firstname, lastname, claimed_status) "
            "VALUES (:id, :first, :last, 'unclaimed')"
        ),
        {"id": advisee, "first": "Current", "last": "Advisee"},
    )
    session.execute(
        text(
            "INSERT INTO person_relationships "
            "(type, from_person_id, to_person_id, starts_at, ends_at, "
            " verification_status) "
            "VALUES ('advised_by', :advisee, :advisor, '2010-01-01', "
            "'2012-01-01', 'verified')"
        ),
        {"advisee": advisee, "advisor": advisor},
    )
    session.commit()
    try:
        info = FakeInfo(session)
        out = resolve_expand(None, info, id=encode("person", advisee), on=date.today())
        ids = {node["id"] for node in out["nodes"]}
        assert encode("person", advisor) not in ids
    finally:
        session.execute(
            text("DELETE FROM person_relationships WHERE from_person_id = :a"),
            {"a": advisee},
        )
        session.execute(text("DELETE FROM people WHERE id = :id"), {"id": advisor})
        session.execute(text("DELETE FROM people WHERE id = :id"), {"id": advisee})
        session.commit()
