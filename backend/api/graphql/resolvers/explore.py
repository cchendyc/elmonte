from __future__ import annotations

"""expand / pages field resolvers."""

from datetime import date
from typing import Any

from api.graphql.resolvers.errors import _decode_any, _decode_id
from api.graphql.resolvers.registry import _session, query
from api.repositories.orgs import _roster_count, _roster_page
from api.services.graph import _expand_org, _expand_person
from api.services.names import _full_name, _person_role

@query.field("expand")
def resolve_expand(_obj, info, id: str, on: date | None = None) -> dict[str, Any]:  # noqa: A002
    kind, row_id = _decode_any(id)
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
    row_id = _decode_id(ownerId, "org")
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
