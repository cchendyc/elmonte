from __future__ import annotations

"""GraphQL resolver package.

``query`` (QueryType) and ``date_scalar`` are re-exported for
``api.graphql.app``; every resolver module is imported so its
``@query.field`` registrations run before the schema is built.
"""

from api.graphql.resolvers.errors import _decode_any, _decode_id
from api.graphql.resolvers.explore import resolve_expand, resolve_pages
from api.graphql.resolvers.person import resolve_person, resolve_person_export
from api.graphql.resolvers.perspective import resolve_perspective
from api.graphql.resolvers.projection import resolve_person_coauthor_ties, resolve_projection
from api.graphql.resolvers.registry import _session, query
from api.graphql.resolvers.scalars import _parse_date_literal, _parse_date_value, _serialize_date, date_scalar
from api.graphql.resolvers.search import resolve_search
from api.graphql.resolvers.universities import resolve_org_children, resolve_universities

__all__ = [
    "query",
    "date_scalar",
    "resolve_person",
    "resolve_person_export",
    "resolve_expand",
    "resolve_pages",
    "resolve_projection",
    "resolve_person_coauthor_ties",
    "resolve_universities",
    "resolve_org_children",
    "resolve_search",
    "resolve_perspective",
    "_decode_any",
    "_decode_id",
    "_session",
]
