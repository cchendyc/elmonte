from __future__ import annotations

"""person / personExport field resolvers."""

from datetime import date
from typing import Any

from db.models import Person
from graphql import GraphQLError
from sqlalchemy import text

from api.graphql.resolvers.errors import _decode_id
from api.graphql.resolvers.registry import _session, query
from api.id_codec import encode
from api.repositories.people import _closest_people, _person_brief, _person_publications
from api.services.names import _full_name
from api.services.person import (
    CLOSEST_PEOPLE_LIMIT,
    _career_timeline,
    _person_awards,
    _person_concepts,
    _person_grants,
    _person_relationships_export,
    _person_topics,
    _resolve_person,
)


@query.field("person")
def resolve_person(_obj, info, id: str, on: date | None = None) -> dict[str, Any] | None:
    row_id = _decode_id(id, "person")
    as_of = on or date.today()
    return _resolve_person(_session(info), row_id, as_of)


@query.field("personExport")
def resolve_person_export(_obj, info, personId: str) -> dict[str, Any]:
    row_id = _decode_id(personId, "person")
    session = _session(info)
    on = date.today()

    person = session.get(Person, row_id)
    if person is None:
        raise GraphQLError("personExport: person not found")

    brief = _person_brief(session, row_id, on)
    role = brief["role"] if brief else None
    institution = brief["institution"] if brief else None

    # Aliases
    alias_rows = session.execute(
        text("SELECT alias FROM person_aliases WHERE person_id = :pid ORDER BY alias"),
        {"pid": row_id},
    ).all()
    # NULL aliases would violate the [String!]! schema type.
    aliases = [r[0] for r in alias_rows if r[0] is not None]

    # External identifiers
    ext_rows = session.execute(
        text(
            """
            SELECT provider, external_id
            FROM external_identifiers
            WHERE person_id = :pid
            ORDER BY provider, external_id
            """
        ),
        {"pid": row_id},
    ).mappings().all()
    external_identifiers = [
        {"provider": r["provider"], "externalId": r["external_id"]}
        for r in ext_rows
    ]

    return {
        "id": encode("person", person.id),
        "label": _full_name(person.firstname, person.middlename, person.lastname),
        "firstname": person.firstname,
        "middlename": person.middlename,
        "lastname": person.lastname,
        "biography": person.biography,
        "homepageUrl": person.homepage_url,
        "cvUrl": (
            f"/api/people/{encode('person', person.id)}/cv"
            if person.cv_snapshot_id
            else None
        ),
        "role": role,
        "institution": institution,
        "aliases": aliases,
        "externalIdentifiers": external_identifiers,
        "careerTimeline": _career_timeline(session, row_id),
        "publications": _person_publications(session, row_id, None),
        "closestPeople": _closest_people(session, row_id, on, CLOSEST_PEOPLE_LIMIT),
        "personTopics": _person_topics(session, row_id),
        "personConcepts": _person_concepts(session, row_id),
        "awards": _person_awards(session, row_id),
        "grants": _person_grants(session, row_id),
        "personRelationships": _person_relationships_export(session, row_id),
    }
