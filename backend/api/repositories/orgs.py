from __future__ import annotations

"""Organization data access (temporal org tree, rosters, ancestry)."""

from datetime import date
from typing import Any

from api.id_codec import encode
from api.services.orgs import _org_node, _org_sublabel
from db.models import Organization, OrgRelationship
from sqlalchemy import select, text
from sqlalchemy.orm import Session


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
        .order_by(
            text(
                "org_relationships.starts_at DESC NULLS LAST, "
                "org_relationships.id DESC"
            )
        )
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
    """Nearest university ancestor as of ``on``.

    Today's path uses the precomputed ``org_tree_current`` matview (fast path
    for the live map/profile).  Historical dates walk the temporal
    ``org_relationships`` edges directly because the matview bakes in
    CURRENT_DATE and cannot answer as-of queries.
    """
    org = session.get(Organization, org_id)
    if org is None:
        return None
    if org.kind == "university":
        return org

    if on == date.today():
        return session.execute(
            select(Organization).from_statement(
                text(
                    """
                    SELECT organizations.*
                    FROM org_tree_current t
                    JOIN organizations ON organizations.id = ANY(t.ancestor_ids)
                    WHERE t.organization_id = :oid
                      AND organizations.kind = 'university'
                    ORDER BY array_position(t.ancestor_ids, organizations.id)
                    LIMIT 1
                    """
                )
            ),
            {"oid": org_id},
        ).scalar_one_or_none()

    return session.execute(
        select(Organization).from_statement(
            text(
                """
                WITH RECURSIVE walk AS (
                  SELECT o.id, o.kind, 0 AS depth, ARRAY[o.id] AS path
                  FROM organizations o
                  WHERE o.id = :oid

                  UNION ALL

                  SELECT p.id, p.kind, w.depth + 1, w.path || p.id
                  FROM walk w
                  JOIN org_relationships r ON r.child_org_id = w.id
                    AND r.relationship_type = 'primary'
                    AND r.validity @> :as_of
                  JOIN organizations p ON p.id = r.parent_org_id
                  WHERE NOT p.id = ANY(w.path)
                )
                SELECT organizations.*
                FROM walk
                JOIN organizations ON organizations.id = walk.id
                WHERE walk.kind = 'university'
                ORDER BY walk.depth
                LIMIT 1
                """
            )
        ),
        {"oid": org_id, "as_of": on},
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
def _subtree_people_count(session: Session, org_id: int, on: date | None = None) -> int:
    """Total chart-anchored people in an org and all its descendants as of ``on``.

    Today uses the precomputed ``org_tree_current`` matview (fast path).
    Historical dates cannot use that matview because it bakes in
    CURRENT_DATE, so the same tree is walked explicitly against
    ``org_relationships`` / ``affiliation_org_assignments`` and counted
    exactly for the requested date.
    """
    as_of = on or date.today()
    if as_of == date.today():
        row = session.execute(
            text(
                "SELECT subtree_person_count FROM org_tree_current "
                "WHERE organization_id = :oid"
            ),
            {"oid": org_id},
        ).mappings().first()
        return int(row["subtree_person_count"]) if row else 0

    row = session.execute(
        text(
            """
            WITH RECURSIVE current_edges AS (
              SELECT child_org_id, parent_org_id
              FROM org_relationships
              WHERE relationship_type = 'primary'
                AND validity @> :as_of
            ),
            subtree AS (
              SELECT CAST(:oid AS BIGINT) AS organization_id,
                     ARRAY[CAST(:oid AS BIGINT)] AS path
              UNION ALL
              SELECT e.child_org_id, s.path || e.child_org_id
              FROM subtree s
              JOIN current_edges e ON e.parent_org_id = s.organization_id
              WHERE NOT e.child_org_id = ANY(s.path)
            )
            SELECT count(DISTINCT pa.person_id)::int AS n
            FROM affiliation_org_assignments aoa
            JOIN subtree s ON s.organization_id = aoa.organization_id
            JOIN person_affiliations pa
              ON pa.id = aoa.affiliation_id
             AND pa.validity @> :as_of
            WHERE aoa.assignment_type = 'chart_anchor'
            """
        ),
        {"oid": org_id, "as_of": as_of},
    ).scalar_one()
    return int(row)


def _org_external_identifiers(
    session: Session, org_id: int
) -> list[dict[str, str]]:
    """External identifiers for an organization (ROR, GRID, Wikidata, URL)."""
    rows = session.execute(
        text(
            """
            SELECT provider, external_id
            FROM external_identifiers
            WHERE organization_id = :oid
            ORDER BY provider, external_id
            """
        ),
        {"oid": org_id},
    ).mappings().all()
    return [
        {"provider": row["provider"], "externalId": row["external_id"]}
        for row in rows
    ]


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
    chain_ids = [unit.id for unit in chain]
    child_counts = _children_counts(session, chain_ids, on)
    roster_counts = _roster_counts(session, chain_ids, on)
    for unit in chain:
        add_node(
            _org_node(
                unit,
                inst,
                _org_sublabel(
                    unit.kind,
                    child_counts.get(unit.id, 0),
                    roster_counts.get(unit.id, 0),
                ),
            ),
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
