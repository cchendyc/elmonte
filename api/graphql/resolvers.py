"""GraphQL resolvers.

Everything is a synchronous function operating on a SQLAlchemy session that
we open per request in `context_value` and close via the ASGI response
lifecycle.

Return shapes are plain dicts matching the SDL field names. Ariadne handles
serialization; there's no separate Pydantic layer.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from ariadne import QueryType, ScalarType
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from api.id_codec import decode, encode
from scripts.embed.build_projection import (
    DEFAULT_KNN,
    projection_display_edges,
)
from db.models import (
    Organization,
    OrgRelationship,
    Person,
    PersonRelationship,
)


# ---------------------------------------------------------------------------
# constants
# ---------------------------------------------------------------------------

INLINE_ROSTER_LIMIT = 8
PAGE_SIZE_DEFAULT = 24
COAUTHOR_LIMIT = 6
CLOSEST_PEOPLE_LIMIT = 16
COLLEAGUE_LIMIT = 8
PUBLICATION_LIMIT = 40
EVERYONE_KEY = "all"
EVERYONE_LABEL = "People"
# Matches `DEFAULT_KNN` in scripts/embed/build_projection.py
PROJECTION_EDGE_KNN = DEFAULT_KNN

# Coarse seniority prior — citations dominate when bibliography data exists.
_RANK_IMPACT: dict[str, float] = {
    "dean": 1.0,
    "executive": 0.98,
    "board_member": 0.96,
    "department_head": 0.94,
    "principal_investigator": 0.92,
    "group_leader": 0.9,
    "full_professor": 0.88,
    "emeritus_professor": 0.86,
    "associate_professor": 0.78,
    "assistant_professor": 0.68,
    "adjunct_professor": 0.55,
    "lecturer": 0.5,
    "research_scientist": 0.48,
    "staff_scientist": 0.46,
    "research_fellow": 0.42,
    "postdoc": 0.32,
    "phd_student": 0.18,
    "masters_student": 0.12,
    "undergraduate": 0.08,
    "visiting_student": 0.15,
    "visiting": 0.4,
    "technician": 0.25,
    "engineer": 0.28,
}


# ---------------------------------------------------------------------------
# helpers — shape-only, no DB access
# ---------------------------------------------------------------------------


def _full_name(first: str | None, middle: str | None, last: str | None) -> str:
    parts = [p for p in (first, middle, last) if p]
    return " ".join(parts) if parts else "(unnamed)"


def _person_role(title: str | None) -> str | None:
    return title.strip() if title and title.strip() else None


def _org_label(unit: Organization, institution: Organization | None) -> str:
    if unit.short_name:
        return unit.short_name
    if institution and institution.id != unit.id:
        for pfx in (institution.short_name, institution.name):
            if pfx and unit.name.lower().startswith(pfx.lower() + " "):
                return unit.name[len(pfx):].lstrip()
    return unit.name


def _org_sublabel(kind: str, child_count: int, roster_count: int) -> str:
    parts: list[str] = []
    if child_count:
        parts.append(f"{child_count} unit" + ("" if child_count == 1 else "s"))
    if roster_count:
        parts.append(f"{roster_count} " + ("person" if roster_count == 1 else "people"))
    return f"{kind} · " + " · ".join(parts) if parts else kind


def _projection_graph_edges(
    session: Session,
    person_ids: list[int],
    knn: int = PROJECTION_EDGE_KNN,
) -> list[dict[str, Any]]:
    sparse = projection_display_edges(session, person_ids, knn=knn)
    return [
        {
            "sourceId": encode("person", a),
            "targetId": encode("person", b),
            "weight": weight,
        }
        for a, b, weight in sparse
    ]


def _structural_weights(edges: list[dict[str, Any]]) -> dict[str, float]:
    weight: dict[str, float] = {}
    for edge in edges:
        w = float(edge["weight"])
        src = edge["sourceId"]
        tgt = edge["targetId"]
        weight[src] = weight.get(src, 0.0) + w
        weight[tgt] = weight.get(tgt, 0.0) + w
    return weight


def _raw_person_impact(
    citation_count: int,
    publication_count: int,
    rank: str | None,
    structural_weight: float,
) -> float:
    rank_s = _RANK_IMPACT.get(rank or "", 0.4)
    cite_s = math.log1p(max(0, citation_count)) * 4.0
    pub_s = math.log1p(max(0, publication_count)) * 2.0
    struct_s = math.log1p(max(0.0, structural_weight)) * 0.55
    return cite_s + pub_s + rank_s * 2.2 + struct_s


def _normalize_impacts(raw: dict[str, float]) -> dict[str, float]:
    if not raw:
        return {}
    lo = min(raw.values())
    hi = max(raw.values())
    if hi <= lo:
        return {key: 0.5 for key in raw}
    span = hi - lo
    return {key: (value - lo) / span for key, value in raw.items()}


def _org_node(unit: Organization, institution: Organization | None, sublabel: str) -> dict[str, Any]:
    return {
        "id": encode("org", unit.id),
        "kind": "org",
        "label": _org_label(unit, institution),
        "sublabel": sublabel,
        "orgKind": unit.kind,
        "institution": None,
        "rank": None,
        "stub": False,
    }


def _person_node(
    person_id: int,
    firstname: str | None,
    middlename: str | None,
    lastname: str | None,
    title: str | None,
    institution_name: str | None,
    rank: str | None,
    stub: bool = False,
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
    }


# ---------------------------------------------------------------------------
# DB queries
# ---------------------------------------------------------------------------


def _parent_of(session: Session, org_id: int, on: date) -> Organization | None:
    return session.execute(
        select(Organization)
        .join(OrgRelationship, OrgRelationship.parent_org_id == Organization.id)
        .where(
            OrgRelationship.child_org_id == org_id,
            OrgRelationship.relationship_type == "primary",
            text("org_relationships.validity @> :as_of"),
        )
        .params(as_of=on)
        .limit(1)
    ).scalar_one_or_none()


def _children_of(session: Session, org_id: int, on: date) -> list[Organization]:
    return list(
        session.execute(
            select(Organization)
            .join(OrgRelationship, OrgRelationship.child_org_id == Organization.id)
            .where(
                OrgRelationship.parent_org_id == org_id,
                OrgRelationship.relationship_type == "primary",
                text("org_relationships.validity @> :as_of"),
            )
            .params(as_of=on)
            .order_by(Organization.name)
        ).scalars()
    )


def _institution_of(session: Session, org_id: int, on: date) -> Organization | None:
    cur = session.get(Organization, org_id)
    steps = 0
    while cur and cur.kind != "university" and steps < 8:
        parent = _parent_of(session, cur.id, on)
        if parent is None:
            break
        cur = parent
        steps += 1
    return cur if cur and cur.kind == "university" else None


def _roster_count(session: Session, org_id: int, on: date) -> int:
    return int(
        session.execute(
            text(
                "SELECT count(*) FROM org_current_roster "
                "WHERE organization_id = :oid AND validity @> :as_of"
            ),
            {"oid": org_id, "as_of": on},
        ).scalar_one()
    )


def _roster_page(
    session: Session,
    org_id: int,
    on: date,
    offset: int,
    limit: int,
) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
              r.person_id,
              r.firstname,
              r.middlename,
              r.lastname,
              r.title,
              r.position_rank
            FROM org_current_roster r
            WHERE r.organization_id = :oid
              AND r.validity @> :as_of
            ORDER BY r.sort_key, r.person_id
            OFFSET :offset LIMIT :limit
            """
        ),
        {"oid": org_id, "as_of": on, "offset": offset, "limit": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def _anchor_of(session: Session, person_id: int, on: date) -> dict[str, Any] | None:
    row = session.execute(
        text(
            """
            SELECT
              pa.affiliation_id,
              pa.title,
              pa.position_rank,
              pa.is_primary,
              pa.organization_id
            FROM person_anchor pa
            WHERE pa.person_id = :pid
              AND pa.validity @> :as_of
            ORDER BY pa.is_primary DESC
            LIMIT 1
            """
        ),
        {"pid": person_id, "as_of": on},
    ).mappings().first()
    return dict(row) if row else None


def _top_coauthors(session: Session, person_id: int, limit: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            (SELECT person_b AS other_id, paper_count
             FROM person_coauthor_edges
             WHERE person_a = :pid
             ORDER BY paper_count DESC LIMIT :lim)
            UNION ALL
            (SELECT person_a AS other_id, paper_count
             FROM person_coauthor_edges
             WHERE person_b = :pid
             ORDER BY paper_count DESC LIMIT :lim)
            ORDER BY paper_count DESC LIMIT :lim
            """
        ),
        {"pid": person_id, "lim": limit},
    ).mappings().all()
    return [dict(r) for r in rows]


def _person_brief(
    session: Session,
    person_id: int,
    on: date,
) -> dict[str, Any] | None:
    person = session.get(Person, person_id)
    if person is None:
        return None
    anchor = _anchor_of(session, person_id, on)
    inst_name: str | None = None
    if anchor and anchor.get("organization_id"):
        inst = _institution_of(session, anchor["organization_id"], on)
        inst_name = inst.name if inst else None
    return {
        "person": person,
        "anchor": anchor,
        "institution": inst_name,
        "role": _person_role(anchor.get("title") if anchor else None),
        "rank": anchor.get("position_rank") if anchor else None,
    }


def _career_timeline(session: Session, person_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
              pa.title,
              pa.affiliation_kind,
              pa.position_rank,
              pa.is_primary,
              pa.starts_at,
              pa.ends_at,
              org.name       AS org_name,
              org.short_name AS org_short_name
            FROM person_affiliations pa
            LEFT JOIN LATERAL (
              SELECT o.name, o.short_name
              FROM affiliation_org_assignments aoa
              JOIN organizations o ON o.id = aoa.organization_id
              WHERE aoa.affiliation_id = pa.id
              ORDER BY
                CASE aoa.assignment_type
                  WHEN 'chart_anchor' THEN 0
                  ELSE 1
                END,
                aoa.organization_id
              LIMIT 1
            ) org ON TRUE
            WHERE pa.person_id = :pid
            ORDER BY pa.starts_at DESC NULLS LAST, pa.id DESC
            """
        ),
        {"pid": person_id},
    ).mappings().all()
    entries: list[dict[str, Any]] = []
    for row in rows:
        org = row["org_short_name"] or row["org_name"] or "Organization not linked"
        starts = row["starts_at"].date() if row["starts_at"] else None
        ends = row["ends_at"].date() if row["ends_at"] else None
        entries.append(
            {
                "title": _person_role(row["title"]),
                "organization": org,
                "affiliationKind": row["affiliation_kind"],
                "positionRank": row["position_rank"],
                "isPrimary": bool(row["is_primary"]),
                "startsAt": starts,
                "endsAt": ends,
            }
        )
    return entries


def _colleagues_at_anchor(
    session: Session,
    person_id: int,
    on: date,
    limit: int,
) -> list[dict[str, Any]]:
    anchor = _anchor_of(session, person_id, on)
    if not anchor or not anchor.get("organization_id"):
        return []
    rows = session.execute(
        text(
            """
            SELECT
              r.person_id,
              r.firstname,
              r.middlename,
              r.lastname,
              r.title,
              r.position_rank
            FROM org_current_roster r
            WHERE r.organization_id = :oid
              AND r.person_id <> :pid
              AND r.validity @> :as_of
            ORDER BY r.sort_key, r.person_id
            LIMIT :lim
            """
        ),
        {
            "oid": anchor["organization_id"],
            "pid": person_id,
            "as_of": on,
            "lim": limit,
        },
    ).mappings().all()
    return [dict(r) for r in rows]


def _person_publications(session: Session, person_id: int, limit: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
              pub.id,
              pub.title,
              pub.publication_year,
              pub.cited_by_count,
              pa.author_position
            FROM publication_authors pa
            JOIN publications pub ON pub.id = pa.publication_id
            WHERE pa.person_id = :pid
            ORDER BY pub.publication_year DESC, pub.id DESC
            LIMIT :lim
            """
        ),
        {"pid": person_id, "lim": limit},
    ).mappings().all()
    return [
        {
            "id": f"pub:{int(row['id'])}",
            "title": row["title"],
            "year": int(row["publication_year"]),
            "citedByCount": int(row["cited_by_count"]) if row["cited_by_count"] is not None else None,
            "authorPosition": int(row["author_position"]),
        }
        for row in rows
    ]


def _person_relations_all(
    session: Session, person_id: int, kind: str
) -> list[PersonRelationship]:
    return list(
        session.execute(
            select(PersonRelationship).where(
                PersonRelationship.type == kind,
                or_(
                    PersonRelationship.from_person_id == person_id,
                    PersonRelationship.to_person_id == person_id,
                ),
            )
        ).scalars()
    )


def _closest_people(session: Session, person_id: int, on: date, limit: int) -> list[dict[str, Any]]:
    seen: set[int] = {person_id}
    results: list[dict[str, Any]] = []

    def add_hit(
        other_id: int,
        relation: str,
        detail: str | None,
        priority: int,
        *,
        label: str | None = None,
        role: str | None = None,
        institution: str | None = None,
    ) -> None:
        if other_id in seen:
            return
        if label is None:
            brief = _person_brief(session, other_id, on)
            if brief is None:
                return
            person = brief["person"]
            label = _full_name(person.firstname, person.middlename, person.lastname)
            role = brief["role"]
            institution = brief["institution"]
        seen.add(other_id)
        results.append(
            {
                "id": encode("person", other_id),
                "label": label,
                "role": role,
                "institution": institution,
                "relation": relation,
                "detail": detail,
                "_priority": priority,
            }
        )

    for rel in _person_relations_all(session, person_id, "advised_by"):
        if rel.from_person_id == person_id:
            add_hit(rel.to_person_id, "advisor", None, 0)
        else:
            add_hit(rel.from_person_id, "advisee", None, 1)

    for row in _top_coauthors(session, person_id, limit):
        weight = int(row["paper_count"])
        add_hit(
            row["other_id"],
            "coauthor",
            f"{weight} paper" + ("" if weight == 1 else "s"),
            10 - min(weight, 9),
        )

    for row in _colleagues_at_anchor(session, person_id, on, COLLEAGUE_LIMIT):
        add_hit(
            row["person_id"],
            "colleague",
            "Same unit",
            20,
            label=_full_name(row["firstname"], row["middlename"], row["lastname"]),
            role=_person_role(row["title"]),
            institution=None,
        )

    results.sort(key=lambda item: (item["_priority"], item["label"]))
    for item in results:
        item.pop("_priority", None)
    return results[:limit]


def _resolve_person(session: Session, person_id: int, on: date) -> dict[str, Any] | None:
    brief = _person_brief(session, person_id, on)
    if brief is None:
        return None
    person = brief["person"]
    return {
        "id": encode("person", person.id),
        "label": _full_name(person.firstname, person.middlename, person.lastname),
        "role": brief["role"],
        "institution": brief["institution"],
        "biography": person.biography,
        "homepageUrl": person.homepage_url,
        "careerTimeline": _career_timeline(session, person_id),
        "publications": _person_publications(session, person_id, PUBLICATION_LIMIT),
        "closestPeople": _closest_people(session, person_id, on, CLOSEST_PEOPLE_LIMIT),
    }


def _person_relations(session: Session, person_id: int, kind: str) -> list[PersonRelationship]:
    return list(
        session.execute(
            select(PersonRelationship).where(
                PersonRelationship.type == kind,
                or_(
                    PersonRelationship.from_person_id == person_id,
                    PersonRelationship.to_person_id == person_id,
                ),
                text("person_relationships.validity @> CURRENT_DATE"),
            )
        ).scalars()
    )


# ---------------------------------------------------------------------------
# expand: person and org branches
# ---------------------------------------------------------------------------


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

    anchor = _anchor_of(session, person_id, on)
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
        )
    )

    if anchor_org:
        add_node(
            _org_node(
                anchor_org,
                institution,
                _org_sublabel(anchor_org.kind, 0, _roster_count(session, anchor_org.id, on)),
            )
        )
        links.append(
            {
                "source": encode("org", anchor_org.id),
                "target": encode("person", person.id),
                "relation": "placement",
                "weight": None,
                "label": None,
            }
        )
        parent = _parent_of(session, anchor_org.id, on)
        if parent:
            add_node(_org_node(parent, institution, _org_sublabel(parent.kind, 1, 0)))
            links.append(
                {
                    "source": encode("org", parent.id),
                    "target": encode("org", anchor_org.id),
                    "relation": "org_parent",
                    "weight": None,
                    "label": None,
                }
            )

    for rel in _person_relations(session, person_id, "advised_by"):
        advisor_id = (
            rel.to_person_id if rel.from_person_id == person_id else rel.from_person_id
        )
        advisor = session.get(Person, advisor_id)
        if not advisor:
            continue
        adv_anchor = _anchor_of(session, advisor.id, on)
        adv_inst: str | None = None
        adv_rank = adv_anchor.get("position_rank") if adv_anchor else None
        if adv_anchor and adv_anchor.get("organization_id"):
            inst = _institution_of(session, adv_anchor["organization_id"], on)
            adv_inst = inst.name if inst else None
        add_node(
            _person_node(
                advisor.id,
                advisor.firstname,
                advisor.middlename,
                advisor.lastname,
                adv_anchor.get("title") if adv_anchor else None,
                adv_inst,
                adv_rank,
                stub=True,
            )
        )
        directed_source = (
            rel.to_person_id if rel.from_person_id == person_id else rel.from_person_id
        )
        directed_target = person_id if directed_source == advisor_id else advisor_id
        links.append(
            {
                "source": encode("person", directed_source),
                "target": encode("person", directed_target),
                "relation": "report",
                "weight": None,
                "label": None,
            }
        )

    for row in _top_coauthors(session, person_id, COAUTHOR_LIMIT):
        other_id = row["other_id"]
        if encode("person", other_id) in nodes:
            continue
        other = session.get(Person, other_id)
        if not other:
            continue
        other_anchor = _anchor_of(session, other.id, on)
        other_inst: str | None = None
        other_rank = other_anchor.get("position_rank") if other_anchor else None
        if other_anchor and other_anchor.get("organization_id"):
            inst = _institution_of(session, other_anchor["organization_id"], on)
            other_inst = inst.name if inst else None
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
            )
        )
        weight = int(row["paper_count"])
        links.append(
            {
                "source": encode("person", person.id),
                "target": encode("person", other.id),
                "relation": "coauthor",
                "weight": weight,
                "label": f"{weight} paper" + ("" if weight == 1 else "s"),
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

    parent = _parent_of(session, org_id, on)
    if parent:
        add_node(_org_node(parent, institution, _org_sublabel(parent.kind, 1, 0)))
        links.append(
            {
                "source": encode("org", parent.id),
                "target": encode("org", unit.id),
                "relation": "org_parent",
                "weight": None,
                "label": None,
            }
        )

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

query = QueryType()

date_scalar = ScalarType("Date")


@date_scalar.serializer
def _serialize_date(value: date) -> str:
    return value.isoformat()


@date_scalar.value_parser
def _parse_date_value(value: str) -> date:
    return date.fromisoformat(value)


@date_scalar.literal_parser
def _parse_date_literal(ast: Any) -> date:
    return date.fromisoformat(ast.value)


def _session(info: Any) -> Session:
    return info.context["db"]


@query.field("person")
def resolve_person(_obj, info, id: str, on: date | None = None) -> dict[str, Any] | None:  # noqa: A002
    kind, row_id = decode(id)
    if kind != "person":
        raise ValueError("person: id must be a person id")
    as_of = on or date.today()
    return _resolve_person(_session(info), row_id, as_of)


@query.field("expand")
def resolve_expand(_obj, info, id: str, on: date | None = None) -> dict[str, Any]:  # noqa: A002
    kind, row_id = decode(id)
    as_of = on or date.today()
    session = _session(info)
    if kind == "person":
        return _expand_person(session, row_id, as_of)
    return _expand_org(session, row_id, as_of)


@query.field("pages")
def resolve_pages(
    _obj,
    info,
    ownerId: str,
    groupKey: str = "all",
    offset: int = 0,
    limit: int = 24,
    on: date | None = None,
) -> dict[str, Any]:
    kind, row_id = decode(ownerId)
    if kind != "org":
        raise ValueError("pages: ownerId must be an org id")
    as_of = on or date.today()
    session = _session(info)
    total = _roster_count(session, row_id, as_of)
    items = _roster_page(session, row_id, as_of, offset=offset, limit=limit)
    return {
        "ownerId": ownerId,
        "groupKey": groupKey,
        "offset": offset + len(items),
        "total": total,
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


@query.field("projection")
def resolve_projection(_obj, info) -> dict[str, Any]:
    session = _session(info)
    active = session.execute(
        text(
            """
            SELECT id, algorithm, point_count
            FROM embedding_runs
            WHERE is_active
            """
        )
    ).mappings().first()
    if active is None:
        return {"runId": "", "algorithm": "", "pointCount": 0, "points": [], "edges": []}

    # One query joins the projection to the person's anchor org (for
    # cluster colour) and up the org tree to the containing institution
    # (for the readable label). We take the anchor's ancestor labelled as
    # 'university' where possible so intra-university clusters share a
    # colour.
    rows = session.execute(
        text(
            """
            SELECT
              p.person_id,
              p.x,
              p.y,
              pe.firstname,
              pe.middlename,
              pe.lastname,
              pa.title,
              pa.position_rank,
              pa.organization_id AS anchor_org_id,
              inst.id            AS institution_id,
              inst.name          AS institution_name,
              coalesce(impact.publication_count, 0) AS publication_count,
              coalesce(impact.citation_count, 0)    AS citation_count
            FROM person_projections_2d p
            JOIN people pe ON pe.id = p.person_id
            LEFT JOIN person_anchor pa
              ON pa.person_id = p.person_id
             AND pa.validity @> CURRENT_DATE
             AND pa.is_primary
            LEFT JOIN LATERAL (
              SELECT o.id, o.name
              FROM org_tree_current t
              JOIN organizations o ON o.id = ANY(t.ancestor_ids)
              WHERE t.organization_id = pa.organization_id
                AND o.kind = 'university'
              LIMIT 1
            ) inst ON TRUE
            LEFT JOIN LATERAL (
              SELECT
                count(*)::int AS publication_count,
                coalesce(sum(pub.cited_by_count), 0)::int AS citation_count
              FROM publication_authors pa_pub
              JOIN publications pub ON pub.id = pa_pub.publication_id
              WHERE pa_pub.person_id = p.person_id
            ) impact ON TRUE
            WHERE p.run_id = :run_id
            """
        ),
        {"run_id": active["id"]},
    ).mappings().all()

    person_ids = [int(r["person_id"]) for r in rows]
    edges = _projection_graph_edges(session, person_ids)
    structural = _structural_weights(edges)
    raw_impact: dict[str, float] = {}
    for r in rows:
        pid = encode("person", int(r["person_id"]))
        raw_impact[pid] = _raw_person_impact(
            int(r["citation_count"]),
            int(r["publication_count"]),
            r["position_rank"],
            structural.get(pid, 0.0),
        )
    impact_by_id = _normalize_impacts(raw_impact)

    points = [
        {
            "id": encode("person", int(r["person_id"])),
            "label": _full_name(r["firstname"], r["middlename"], r["lastname"]),
            "x": float(r["x"]),
            "y": float(r["y"]),
            "institution": r["institution_name"],
            "institutionId": (
                encode("org", int(r["institution_id"]))
                if r["institution_id"] is not None
                else None
            ),
            "rank": r["position_rank"],
            "impact": impact_by_id[encode("person", int(r["person_id"]))],
        }
        for r in rows
    ]
    return {
        "runId": str(active["id"]),
        "algorithm": active["algorithm"],
        "pointCount": len(points),
        "points": points,
        "edges": edges,
    }


@query.field("search")
def resolve_search(_obj, info, q: str, limit: int = 10) -> dict[str, Any]:
    query_text = q.strip()
    if not query_text:
        return {"people": [], "orgs": []}
    session = _session(info)
    as_of = date.today()

    person_rows = session.execute(
        text(
            """
            SELECT
              p.id,
              p.firstname,
              p.middlename,
              p.lastname,
              pa.title,
              o.name AS institution_name,
              GREATEST(
                similarity(p.firstname, :q),
                similarity(p.lastname, :q),
                similarity(coalesce(p.firstname,'') || ' ' || coalesce(p.lastname,''), :q),
                coalesce((
                  SELECT max(similarity(a.alias, :q))
                  FROM person_aliases a
                  WHERE a.person_id = p.id
                ), 0)
              ) AS score
            FROM people p
            LEFT JOIN person_anchor pa
              ON pa.person_id = p.id
             AND pa.validity @> :as_of
             AND pa.is_primary
            LEFT JOIN organizations o
              ON o.id = pa.organization_id
            WHERE
                 p.firstname % :q
              OR p.lastname  % :q
              OR (coalesce(p.firstname,'') || ' ' || coalesce(p.lastname,'')) % :q
              OR EXISTS (
                SELECT 1
                FROM person_aliases a
                WHERE a.person_id = p.id
                  AND a.alias % :q
              )
            ORDER BY score DESC
            LIMIT :lim
            """
        ),
        {"q": query_text, "as_of": as_of, "lim": limit},
    ).mappings().all()

    people = [
        {
            "id": encode("person", row["id"]),
            "label": _full_name(row["firstname"], row["middlename"], row["lastname"]),
            "role": row["title"],
            "institution": row["institution_name"],
        }
        for row in person_rows
    ]

    org_rows = session.execute(
        text(
            """
            SELECT id, name, short_name, kind, similarity(name, :q) AS score
            FROM organizations
            WHERE name % :q OR (short_name IS NOT NULL AND short_name % :q)
            ORDER BY score DESC
            LIMIT :lim
            """
        ),
        {"q": query_text, "lim": limit},
    ).mappings().all()

    orgs = [
        {
            "id": encode("org", row["id"]),
            "label": row["short_name"] or row["name"],
            "orgKind": row["kind"],
        }
        for row in org_rows
    ]
    return {"people": people, "orgs": orgs}
