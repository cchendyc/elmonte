from __future__ import annotations

"""person / personExport field resolvers."""

from datetime import date
from typing import Any

from graphql import GraphQLError
from sqlalchemy import text

from api.id_codec import encode
from db.models import Person

from api.graphql.resolvers.errors import _decode_id
from api.graphql.resolvers.registry import _session, query
from api.graphql.resolvers.scalars import _parse_date_value
from api.repositories.people import _closest_people, _person_brief
from api.services.names import _full_name
from api.services.person import CLOSEST_PEOPLE_LIMIT, _career_timeline, _resolve_person
from scripts.backfill.common import is_displayable_publication

@query.field("person")
def resolve_person(_obj, info, id: str, on: date | None = None) -> dict[str, Any] | None:  # noqa: A002
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

    # Publications — unlimited (use a large limit)
    pub_rows = session.execute(
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
            LIMIT 10000
            """
        ),
        {"pid": row_id},
    ).mappings().all()
    publications = [
        {
            "id": f"pub:{int(row['id'])}",
            "title": row["title"],
            "year": int(row["publication_year"]),
            "citedByCount": int(row["cited_by_count"]) if row["cited_by_count"] is not None else None,
            "authorPosition": int(row["author_position"]),
        }
        for row in pub_rows
        if is_displayable_publication(row["title"])
    ]

    # Person topics (with display_name)
    topic_rows = session.execute(
        text(
            """
            SELECT t.display_name, pt.score, pt.works_count
            FROM person_topics pt
            JOIN topics t ON t.openalex_topic_id = pt.topic_id
            WHERE pt.person_id = :pid
            ORDER BY pt.score DESC
            """
        ),
        {"pid": row_id},
    ).mappings().all()
    person_topics = [
        {
            "displayName": r["display_name"],
            "score": float(r["score"]),
            "worksCount": int(r["works_count"]),
        }
        for r in topic_rows
    ]

    # Person concepts (with display_name)
    concept_rows = session.execute(
        text(
            """
            SELECT c.display_name, pc.score, pc.rank
            FROM person_concepts pc
            JOIN concepts c ON c.id = pc.concept_id
            WHERE pc.person_id = :pid
            ORDER BY pc.rank ASC NULLS LAST
            """
        ),
        {"pid": row_id},
    ).mappings().all()
    person_concepts = [
        {
            "displayName": r["display_name"],
            "score": float(r["score"]) if r["score"] is not None else None,
            "rank": int(r["rank"]) if r["rank"] is not None else None,
        }
        for r in concept_rows
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
        "publications": publications,
        "closestPeople": _closest_people(session, row_id, on, CLOSEST_PEOPLE_LIMIT),
        "personTopics": person_topics,
        "personConcepts": person_concepts,
    }
