from __future__ import annotations

"""Ego-network graph services: person/org expansion + perspective grouping.

Composes repositories with pure node builders; igraph is imported lazily
inside _alter_groups so the module stays importable without the extension.
"""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.id_codec import encode
from db.models import Organization, Person
from api.repositories.orgs import _append_org_ancestry, _children_of, _institution_of, _roster_count, _roster_page
from api.repositories.people import _anchor_context, _person_brief, _person_relations_all, _top_coauthors
from api.services.names import _full_name, _person_role
from api.services.orgs import _org_node, _org_sublabel

INLINE_ROSTER_LIMIT = 8
PAGE_SIZE_DEFAULT = 24
COAUTHOR_LIMIT = 6

EVERYONE_KEY = "all"
EVERYONE_LABEL = "People"

def _person_node(
    person_id: int,
    firstname: str | None,
    middlename: str | None,
    lastname: str | None,
    title: str | None,
    institution_name: str | None,
    rank: str | None,
    stub: bool = False,
    retired_at: date | None = None,
) -> dict[str, Any]:
    return {
        "id": encode("person", person_id),
        "kind": "person",
        "label": _full_name(firstname, middlename, lastname),
        "sublabel": _person_role(title),
        "institution": institution_name,
        "orgKind": None,
        "rank": rank,
        "stub": stub,
        "retiredAt": retired_at,
    }
def _append_supervision_neighborhood(
    session: Session,
    person_id: int,
    on: date,
    institution_name: str | None,
    add_node: Any,
    links: list[dict[str, Any]],
) -> None:
    """One hop of advisor / advisee links for the top-down person trace."""
    for rel in _person_relations_all(session, person_id, "advised_by"):
        if rel.from_person_id == person_id:
            advisor_id = rel.to_person_id
            brief = _person_brief(session, advisor_id, on)
            if brief is None:
                continue
            person = brief["person"]
            add_node(
                _person_node(
                    person.id,
                    person.firstname,
                    person.middlename,
                    person.lastname,
                    brief["role"],
                    institution_name,
                    brief.get("rank"),
                )
            )
            links.append(
                {
                    "source": encode("person", advisor_id),
                    "target": encode("person", person_id),
                    "relation": "report",
                    "weight": None,
                    "label": None,
                }
            )
        else:
            advisee_id = rel.from_person_id
            brief = _person_brief(session, advisee_id, on)
            if brief is None:
                continue
            person = brief["person"]
            add_node(
                _person_node(
                    person.id,
                    person.firstname,
                    person.middlename,
                    person.lastname,
                    brief["role"],
                    institution_name,
                    brief.get("rank"),
                )
            )
            links.append(
                {
                    "source": encode("person", person_id),
                    "target": encode("person", advisee_id),
                    "relation": "report",
                    "weight": None,
                    "label": None,
                }
            )
def _expand_person(session: Session, person_id: int, on: date) -> dict[str, Any]:
    person = session.get(Person, person_id)
    if person is None:
        return {
            "focusId": encode("person", person_id),
            "nodes": [],
            "links": [],
            "groups": None,
            "page": None,
        }

    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    def add_node(node: dict[str, Any]) -> None:
        nodes.setdefault(node["id"], node)

    anchor, focus_retired_at = _anchor_context(session, person_id, on)
    focus_rank = anchor.get("position_rank") if anchor else None
    anchor_org: Organization | None = None
    institution: Organization | None = None
    if anchor:
        anchor_org = session.get(Organization, anchor["organization_id"])
        institution = _institution_of(session, anchor_org.id, on) if anchor_org else None

    inst_name = institution.name if institution else None
    add_node(
        _person_node(
            person.id,
            person.firstname,
            person.middlename,
            person.lastname,
            anchor.get("title") if anchor else None,
            inst_name,
            focus_rank,
            retired_at=focus_retired_at,
        )
    )

    if anchor_org:
        _append_org_ancestry(session, anchor_org.id, on, institution, add_node, links)
        links.append(
            {
                "source": encode("org", anchor_org.id),
                "target": encode("person", person.id),
                "relation": "placement",
                "weight": None,
                "label": None,
            }
        )

    _append_supervision_neighborhood(
        session, person.id, on, inst_name, add_node, links
    )

    for row in _top_coauthors(session, person_id, COAUTHOR_LIMIT):
        other_id = int(row["other_id"])
        if other_id == person_id or encode("person", other_id) in nodes:
            continue
        other = session.get(Person, other_id)
        if other is None:
            continue

        other_anchor, other_retired_at = _anchor_context(session, other.id, on)
        other_inst: str | None = None
        other_rank = other_anchor.get("position_rank") if other_anchor else None
        other_anchor_org: Organization | None = None
        other_institution: Organization | None = None
        if other_anchor and other_anchor.get("organization_id"):
            other_anchor_org = session.get(Organization, other_anchor["organization_id"])
            other_institution = (
                _institution_of(session, other_anchor_org.id, on)
                if other_anchor_org
                else None
            )
            other_inst = other_institution.name if other_institution else None

        add_node(
            _person_node(
                other.id,
                other.firstname,
                other.middlename,
                other.lastname,
                other_anchor.get("title") if other_anchor else None,
                other_inst,
                other_rank,
                stub=True,
                retired_at=other_retired_at,
            )
        )

        if other_anchor_org:
            add_node(
                _org_node(
                    other_anchor_org,
                    other_institution,
                    _org_sublabel(
                        other_anchor_org.kind,
                        0,
                        _roster_count(session, other_anchor_org.id, on),
                    ),
                )
            )
            links.append(
                {
                    "source": encode("org", other_anchor_org.id),
                    "target": encode("person", other.id),
                    "relation": "placement",
                    "weight": None,
                    "label": None,
                }
            )

    return {
        "focusId": encode("person", person.id),
        "nodes": list(nodes.values()),
        "links": links,
        "groups": None,
        "page": None,
    }
def _expand_org(session: Session, org_id: int, on: date) -> dict[str, Any]:
    unit = session.get(Organization, org_id)
    if unit is None:
        return {
            "focusId": encode("org", org_id),
            "nodes": [],
            "links": [],
            "groups": None,
            "page": None,
        }

    institution = _institution_of(session, org_id, on)
    children = _children_of(session, org_id, on)
    roster_total = _roster_count(session, org_id, on)

    nodes: dict[str, dict[str, Any]] = {}
    links: list[dict[str, Any]] = []

    def add_node(node: dict[str, Any]) -> None:
        nodes.setdefault(node["id"], node)

    add_node(_org_node(unit, institution, _org_sublabel(unit.kind, len(children), roster_total)))

    _append_org_ancestry(session, org_id, on, institution, add_node, links)

    for child in children:
        child_roster = _roster_count(session, child.id, on)
        add_node(_org_node(child, institution, _org_sublabel(child.kind, 0, child_roster)))
        links.append(
            {
                "source": encode("org", unit.id),
                "target": encode("org", child.id),
                "relation": "org_parent",
                "weight": None,
                "label": None,
            }
        )

    if roster_total == 0:
        return {
            "focusId": encode("org", unit.id),
            "nodes": list(nodes.values()),
            "links": links,
            "groups": None,
            "page": None,
        }

    if roster_total <= INLINE_ROSTER_LIMIT:
        for row in _roster_page(session, org_id, on, offset=0, limit=INLINE_ROSTER_LIMIT):
            add_node(
                _person_node(
                    row["person_id"],
                    row["firstname"],
                    row["middlename"],
                    row["lastname"],
                    row["title"],
                    institution.name if institution else None,
                    row["position_rank"],
                )
            )
            links.append(
                {
                    "source": encode("org", unit.id),
                    "target": encode("person", row["person_id"]),
                    "relation": "placement",
                    "weight": None,
                    "label": None,
                }
            )
        return {
            "focusId": encode("org", unit.id),
            "nodes": list(nodes.values()),
            "links": links,
            "groups": None,
            "page": None,
        }

    items = _roster_page(session, org_id, on, offset=0, limit=PAGE_SIZE_DEFAULT)
    page = {
        "ownerId": encode("org", unit.id),
        "groupKey": EVERYONE_KEY,
        "offset": len(items),
        "total": roster_total,
        "items": [
            {
                "id": encode("person", row["person_id"]),
                "label": _full_name(row["firstname"], row["middlename"], row["lastname"]),
                "sublabel": _person_role(row["title"]),
                "rank": row["position_rank"],
            }
            for row in items
        ],
    }
    groups = [{"key": EVERYONE_KEY, "label": EVERYONE_LABEL, "count": roster_total}]
    return {
        "focusId": encode("org", unit.id),
        "nodes": list(nodes.values()),
        "links": links,
        "groups": groups,
        "page": page,
    }


# ---------------------------------------------------------------------------
# Ariadne bindings
# ---------------------------------------------------------------------------
def _alter_groups(session: Session, alter_ids: list[int]) -> dict[int, int]:
    """Leiden groups over the alter-alter shared-paper subgraph."""
    if len(alter_ids) <= 1:
        return {pid: 0 for pid in alter_ids}
    rows = session.execute(
        text(
            """
            SELECT person_a, person_b, paper_count
            FROM person_coauthor_edges
            WHERE person_a = ANY(:ids) AND person_b = ANY(:ids)
            LIMIT 4000
            """
        ),
        {"ids": alter_ids},
    ).all()
    try:
        import igraph as ig
    except ImportError:  # optional dep — degrade to a single group
        return {pid: 0 for pid in alter_ids}

    g = ig.Graph(n=len(alter_ids))
    idx = {pid: i for i, pid in enumerate(alter_ids)}
    es = [(idx[int(a)], idx[int(b)], float(c)) for a, b, c in rows]
    if es:
        g.add_edges([(a, b) for a, b, _ in es])
        g.es["weight"] = [w for _, _, w in es]
    part = g.community_leiden(
        objective_function="modularity",
        weights="weight" if es else None,
        resolution=1.0,
        n_iterations=2,
    )
    return {pid: int(c) for pid, c in zip(alter_ids, part.membership)}
