"""Integration tests for GraphQL resolvers against the seeded demo DB."""

import pytest
from datetime import date

from api.deps import _SessionLocal
from api.graphql.resolvers.person import resolve_person
from api.graphql.resolvers.perspective import resolve_perspective
from api.graphql.resolvers.projection import resolve_person_coauthor_ties, resolve_projection
from api.graphql.resolvers.search import resolve_search
from api.id_codec import decode, encode
from api.repositories.people import _person_brief, _person_briefs
from graphql import GraphQLError


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
    from api.graphql.resolvers.universities import resolve_org_children, resolve_universities

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
