from __future__ import annotations

"""Organization data access (temporal org tree, rosters, ancestry)."""

from datetime import date
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from api.id_codec import encode

from api.services.orgs import _org_node, _org_sublabel
from db.models import Organization, OrgRelationship

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
    """University ancestor via the precomputed org_tree_current ancestor path.

    (The matview bakes in the current date, so `on` is only kept for call
    compatibility — see the org_tree_current migration note.)
    """
    org = session.get(Organization, org_id)
    if org is None:
        return None
    if org.kind == "university":
        return org
    return session.execute(
        select(Organization).from_statement(
            text(
                """
                SELECT organizations.*
                FROM org_tree_current t
                JOIN organizations ON organizations.id = ANY(t.ancestor_ids)
                WHERE t.organization_id = :oid
                  AND organizations.kind = 'university'
                LIMIT 1
                """
            )
        ),
        {"oid": org_id},
    ).scalar_one_or_none()


def _children_counts(session: Session, org_ids: list[int], on: date) -> dict[int, int]:
    """Child counts for a batch of org ids — one GROUP BY instead of N queries."""
    if not org_ids:
        return {}
    rows = session.execute(
        text(
            """
            SELECT parent_org_id AS org_id, count(*)::int AS n
            FROM org_relationships
            WHERE parent_org_id = ANY(:ids)
              AND relationship_type = 'primary'
              AND validity @> :as_of
            GROUP BY parent_org_id
            """
        ),
        {"ids": org_ids, "as_of": on},
    ).mappings().all()
    return {int(r["org_id"]): int(r["n"]) for r in rows}


def _roster_counts(session: Session, org_ids: list[int], on: date) -> dict[int, int]:
    """Roster sizes for a batch of org ids — one GROUP BY instead of N queries."""
    if not org_ids:
        return {}
    rows = session.execute(
        text(
            """
            SELECT organization_id, count(*)::int AS n
            FROM org_current_roster
            WHERE organization_id = ANY(:ids)
              AND validity @> :as_of
            GROUP BY organization_id
            """
        ),
        {"ids": org_ids, "as_of": on},
    ).mappings().all()
    return {int(r["organization_id"]): int(r["n"]) for r in rows}
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
def _org_ancestry(session: Session, org_id: int, on: date) -> list[Organization]:
    """Root-to-leaf org chain ending at `org_id`."""
    chain: list[Organization] = []
    cur = session.get(Organization, org_id)
    steps = 0
    while cur and steps < 12:
        chain.append(cur)
        if cur.kind == "university":
            break
        parent = _parent_of(session, cur.id, on)
        if parent is None:
            break
        cur = parent
        steps += 1
    chain.reverse()
    return chain
def _append_org_ancestry(
    session: Session,
    org_id: int,
    on: date,
    institution: Organization | None,
    add_node: Any,
    links: list[dict[str, Any]],
) -> None:
    chain = _org_ancestry(session, org_id, on)
    inst = institution or (_institution_of(session, chain[0].id, on) if chain else None)
    for unit in chain:
        child_n = len(_children_of(session, unit.id, on))
        roster = _roster_count(session, unit.id, on)
        add_node(
            _org_node(unit, inst, _org_sublabel(unit.kind, child_n, roster)),
        )
    for i in range(1, len(chain)):
        links.append(
            {
                "source": encode("org", chain[i - 1].id),
                "target": encode("org", chain[i].id),
                "relation": "org_parent",
                "weight": None,
                "label": None,
            }
        )
