from __future__ import annotations

"""universities / orgChildren field resolvers."""

from datetime import date
from typing import Any

from db.models import Organization
from sqlalchemy import select, text

from api.graphql.resolvers.errors import _decode_id
from api.graphql.resolvers.registry import _session, query
from api.id_codec import encode
from api.repositories.orgs import (
    _children_counts,
    _children_of,
    _institution_of,
    _org_external_identifiers,
    _parent_of,
    _roster_count,
    _roster_counts,
    _subtree_people_count,
)
from api.services.orgs import _org_label, _org_sublabel, _org_unit


@query.field("universities")
def resolve_universities(_obj, info, on: date | None = None) -> list[dict[str, Any]]:
    session = _session(info)
    as_of = on or date.today()
    base = select(Organization).where(
        Organization.kind == "university",
        (Organization.is_context_only.is_(None))
        | (Organization.is_context_only.is_(False)),
    )
    if as_of == date.today():
        # The trace picker is most useful when populated universities float to
        # the top; the matview only exists for the current tree, so historical
        # queries use the stable name ordering below.
        units = session.execute(
            select(Organization).from_statement(
                text(
                    """
                    SELECT organizations.*
                    FROM organizations
                    JOIN org_tree_current t ON t.organization_id = organizations.id
                    WHERE organizations.kind = 'university'
                      AND (organizations.is_context_only IS FALSE
                           OR organizations.is_context_only IS NULL)
                    ORDER BY t.subtree_person_count DESC, organizations.name
                    """
                )
            )
        ).scalars().all()
    else:
        units = session.execute(base.order_by(Organization.name)).scalars().all()
    child_counts = _children_counts(session, [u.id for u in units], as_of)
    roster_counts = _roster_counts(session, [u.id for u in units], as_of)
    return [
        _org_unit(
            unit,
            unit,
            child_count=child_counts.get(unit.id, 0),
            roster_count=roster_counts.get(unit.id, 0),
        )
        for unit in units
    ]


@query.field("org")
def resolve_org(_obj, info, id: str, on: date | None = None) -> dict[str, Any] | None:
    row_id = _decode_id(id, "org")
    session = _session(info)
    as_of = on or date.today()
    unit = session.get(Organization, row_id)
    if unit is None:
        return None

    institution = _institution_of(session, unit.id, as_of)
    children = _children_of(session, unit.id, as_of)
    child_ids = [child.id for child in children]
    child_counts = _children_counts(session, child_ids, as_of)
    roster_counts = _roster_counts(session, child_ids, as_of)
    direct_roster = _roster_count(session, unit.id, as_of)

    parent = _parent_of(session, unit.id, as_of)
    parent_unit = None
    if parent is not None:
        parent_ids = [parent.id]
        parent_counts = _children_counts(session, parent_ids, as_of)
        parent_rosters = _roster_counts(session, parent_ids, as_of)
        parent_unit = _org_unit(
            parent,
            institution,
            child_count=parent_counts.get(parent.id, 0),
            roster_count=parent_rosters.get(parent.id, 0),
        )

    return {
        "id": encode("org", unit.id),
        "label": _org_label(unit, institution),
        "name": unit.name,
        "orgKind": unit.kind,
        "sublabel": _org_sublabel(unit.kind, len(children), direct_roster),
        "country": unit.country,
        "homepageUrl": unit.homepage_url,
        "description": unit.description,
        "parent": parent_unit,
        "children": [
            _org_unit(
                child,
                institution,
                child_count=child_counts.get(child.id, 0),
                roster_count=roster_counts.get(child.id, 0),
            )
            for child in children
        ],
        "rosterCount": direct_roster,
        "subtreePeopleCount": _subtree_people_count(session, unit.id, as_of),
        "externalIdentifiers": _org_external_identifiers(session, unit.id),
    }


@query.field("orgChildren")
def resolve_org_children(
    _obj,
    info,
    parentId: str,
    on: date | None = None,
) -> list[dict[str, Any]]:
    row_id = _decode_id(parentId, "org")
    session = _session(info)
    as_of = on or date.today()
    parent = session.get(Organization, row_id)
    if parent is None:
        return []
    institution = _institution_of(session, parent.id, as_of)
    children = _children_of(session, row_id, as_of)
    child_ids = [c.id for c in children]
    child_counts = _children_counts(session, child_ids, as_of)
    roster_counts = _roster_counts(session, child_ids, as_of)
    return [
        _org_unit(
            child,
            institution,
            child_count=child_counts.get(child.id, 0),
            roster_count=roster_counts.get(child.id, 0),
        )
        for child in children
    ]
