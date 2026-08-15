from __future__ import annotations

"""search field resolver."""

from datetime import date
from typing import Any

from sqlalchemy import text

from api.graphql.resolvers.registry import _session, query
from api.id_codec import encode
from api.services.names import _full_name

# Keep trigram queries bounded: a 60 KB query string would otherwise be
# measured against every indexed name and can keep Postgres busy long after
# the 64 KB request body has been accepted.
MAX_SEARCH_QUERY_CHARS = 200
MAX_SEARCH_RESULTS = 50


def _bounded_limit(limit: int) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        return 10
    return max(0, min(limit, MAX_SEARCH_RESULTS))


def _like_literal(value: str) -> str:
    """Escape LIKE/ILIKE wildcards so user input is a literal, not a pattern."""
    return value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@query.field("search")
def resolve_search(_obj, info, q: str, limit: int = 10) -> dict[str, Any]:
    query_text = " ".join(q.split())
    if not query_text:
        return {"people": [], "orgs": []}
    if len(query_text) > MAX_SEARCH_QUERY_CHARS:
        return {"people": [], "orgs": []}
    lim = _bounded_limit(limit)
    if lim == 0:
        return {"people": [], "orgs": []}
    session = _session(info)
    as_of = date.today()

    person_rows = session.execute(
        text(
            """
            WITH alias_scores AS (
              SELECT
                person_id,
                max(
                  CASE
                    WHEN alias % :q THEN similarity(alias, :q)
                    WHEN alias ILIKE '%' || :q_like || '%' THEN 0.45
                    ELSE 0.0
                  END
                ) AS alias_score
              FROM person_aliases
              WHERE alias % :q OR alias ILIKE '%' || :q_like || '%'
              GROUP BY person_id
            ),
            scored AS (
              SELECT
                p.id,
                p.firstname,
                p.middlename,
                p.lastname,
                pa.title,
                coalesce(root.name, o.name) AS institution_name,
                concept.display_name         AS research_area,
                orcid.external_id            AS orcid,
                pub_stats.publication_count,
                pub_stats.last_publication_year,
                similarity(p.firstname, :q) AS sim_first,
                similarity(p.lastname, :q)  AS sim_last,
                similarity(
                  coalesce(p.firstname,'') || ' ' || coalesce(p.lastname,''), :q
                ) AS sim_full,
                coalesce(a.alias_score, 0) AS sim_alias,
                CASE
                  WHEN lower(p.lastname) = lower(:q)
                    OR lower(p.firstname) = lower(:q) THEN 1.0
                  WHEN lower(coalesce(p.firstname,'') || ' ' || coalesce(p.lastname,''))
                       = lower(:q) THEN 0.98
                  WHEN p.lastname ILIKE :q_like || '%'
                    OR p.firstname ILIKE :q_like || '%' THEN 0.70
                  WHEN p.lastname ILIKE '%' || :q_like || '%'
                    OR p.firstname ILIKE '%' || :q_like || '%' THEN 0.55
                  ELSE 0.0
                END AS prefix_score
              FROM people p
              LEFT JOIN person_anchor pa
                ON pa.person_id = p.id
               AND pa.validity @> :as_of
               AND pa.is_primary
              -- A hit's institution is its university home (the atlas/profile
              -- convention), not the leaf lab/department it hangs under.
              LEFT JOIN org_tree_current otc
                ON otc.organization_id = pa.organization_id
              LEFT JOIN organizations root
                ON root.id = otc.root_id
               AND root.kind = 'university'
              LEFT JOIN organizations o
                ON o.id = pa.organization_id
              LEFT JOIN alias_scores a ON a.person_id = p.id
              LEFT JOIN LATERAL (
                SELECT c.display_name
                FROM person_concepts pc
                JOIN concepts c ON c.id = pc.concept_id
                WHERE pc.person_id = p.id
                ORDER BY pc.rank ASC NULLS LAST, c.display_name
                LIMIT 1
              ) concept ON TRUE
              LEFT JOIN LATERAL (
                SELECT external_id
                FROM external_identifiers
                WHERE person_id = p.id AND provider = 'orcid'
                LIMIT 1
              ) orcid ON TRUE
              LEFT JOIN LATERAL (
                SELECT
                  count(*)::int AS publication_count,
                  max(pub.publication_year)::int AS last_publication_year
                FROM publication_authors pub_author
                JOIN publications pub ON pub.id = pub_author.publication_id
                WHERE pub_author.person_id = p.id
              ) pub_stats ON TRUE
              WHERE
                   p.firstname % :q
                OR p.lastname  % :q
                OR (coalesce(p.firstname,'') || ' ' || coalesce(p.lastname,'')) % :q
                OR a.person_id IS NOT NULL
                OR p.firstname ILIKE '%' || :q_like || '%'
                OR p.lastname  ILIKE '%' || :q_like || '%'
                OR (coalesce(p.firstname,'') || ' ' || coalesce(p.lastname,''))
                     ILIKE '%' || :q_like || '%'
            )
            SELECT
              id, firstname, middlename, lastname, title, institution_name,
              research_area, orcid, publication_count, last_publication_year,
              GREATEST(sim_first, sim_last, sim_full, sim_alias, prefix_score) AS score
            FROM scored
            ORDER BY
              score DESC,
              coalesce(lastname, ''),
              coalesce(firstname, ''),
              id
            LIMIT :lim
            """
        ),
        {"q": query_text, "q_like": _like_literal(query_text), "as_of": as_of, "lim": lim},
    ).mappings().all()

    people = [
        {
            "id": encode("person", row["id"]),
            "label": _full_name(row["firstname"], row["middlename"], row["lastname"]),
            "role": row["title"],
            "institution": row["institution_name"],
            "orcid": row["orcid"],
            "researchArea": row["research_area"],
            "publicationCount": int(row["publication_count"])
            if row["publication_count"] is not None
            else 0,
            "lastPublicationYear": int(row["last_publication_year"])
            if row["last_publication_year"] is not None
            else None,
        }
        for row in person_rows
    ]

    org_rows = session.execute(
        text(
            """
            SELECT
              id,
              name,
              short_name,
              kind,
              GREATEST(
                similarity(name, :q),
                CASE
                  WHEN short_name IS NOT NULL AND short_name % :q
                    THEN similarity(short_name, :q)
                  ELSE 0.0
                END,
                CASE
                  WHEN lower(name) = lower(:q)
                    OR (short_name IS NOT NULL AND lower(short_name) = lower(:q))
                    THEN 1.0
                  WHEN name ILIKE :q_like || '%'
                    OR (short_name IS NOT NULL AND short_name ILIKE :q_like || '%')
                    THEN 0.70
                  WHEN name ILIKE '%' || :q_like || '%'
                    OR (short_name IS NOT NULL AND short_name ILIKE '%' || :q_like || '%')
                    THEN 0.55
                  ELSE 0.0
                END
              ) AS score
            FROM organizations
            WHERE (
                   name % :q
                OR (short_name IS NOT NULL AND short_name % :q)
                OR name ILIKE '%' || :q_like || '%'
                OR (short_name IS NOT NULL AND short_name ILIKE '%' || :q_like || '%')
            )
              AND (is_context_only IS FALSE OR is_context_only IS NULL)
            ORDER BY
              score DESC,
              coalesce(short_name, name),
              name,
              id
            LIMIT :lim
            """
        ),
        {"q": query_text, "q_like": _like_literal(query_text), "lim": lim},
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
