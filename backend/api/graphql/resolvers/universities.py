from __future__ import annotations

"""universities / orgChildren field resolvers."""

from datetime import date
from typing import Any

from sqlalchemy import select

from db.models import Organization

from api.graphql.resolvers.errors import _decode_id
from api.graphql.resolvers.registry import _session, query
from api.repositories.orgs import _children_counts, _children_of, _institution_of, _roster_counts
from api.services.orgs import _org_unit

@query.field("universities")
def resolve_universities(_obj, info, on: date | None = None) -> list[dict[str, Any]]:
    session = _session(info)
    as_of = on or date.today()
    units = session.execute(
        select(Organization)
        .where(
            Organization.kind == "university",
            (Organization.is_context_only.is_(None))
            | (Organization.is_context_only.is_(False)),
        )
        .order_by(Organization.name)
    ).scalars().all()
    child_counts = _children_counts(session, [u.id for u in units], as_of)
    return [
        _org_unit(
            unit,
            unit,
            child_count=child_counts.get(unit.id, 0),
        )
        for unit in units
    ]
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
