from __future__ import annotations

"""search field resolver."""

from datetime import date
from typing import Any

from sqlalchemy import text

from api.graphql.resolvers.registry import _session, query
from api.id_codec import encode
from api.services.names import _full_name

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
            WITH alias_scores AS (
              SELECT person_id, max(similarity(alias, :q)) AS alias_score
              FROM person_aliases
              WHERE alias % :q
              GROUP BY person_id
            ),
            scored AS (
              SELECT
                p.id,
                p.firstname,
                p.middlename,
                p.lastname,
                pa.title,
                o.name AS institution_name,
                similarity(p.firstname, :q) AS sim_first,
                similarity(p.lastname, :q)  AS sim_last,
                similarity(
                  coalesce(p.firstname,'') || ' ' || coalesce(p.lastname,''), :q
                ) AS sim_full,
                coalesce(a.alias_score, 0) AS sim_alias
              FROM people p
              LEFT JOIN person_anchor pa
                ON pa.person_id = p.id
               AND pa.validity @> :as_of
               AND pa.is_primary
              LEFT JOIN organizations o
                ON o.id = pa.organization_id
              LEFT JOIN alias_scores a ON a.person_id = p.id
              WHERE
                   p.firstname % :q
                OR p.lastname  % :q
                OR (coalesce(p.firstname,'') || ' ' || coalesce(p.lastname,'')) % :q
                OR a.person_id IS NOT NULL
            )
            SELECT
              id, firstname, middlename, lastname, title, institution_name,
              GREATEST(sim_first, sim_last, sim_full, sim_alias) AS score
            FROM scored
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
            WHERE (name % :q OR (short_name IS NOT NULL AND short_name % :q))
              AND (is_context_only IS FALSE OR is_context_only IS NULL)
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


