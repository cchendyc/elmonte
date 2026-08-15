from __future__ import annotations

"""perspective field resolver."""

from datetime import date
from typing import Any

from sqlalchemy import text

from api.graphql.resolvers.errors import _decode_id
from api.graphql.resolvers.registry import _session, query
from api.id_codec import encode
from api.repositories.people import _person_briefs, _person_relations_active
from api.services.graph import _alter_groups
from api.services.names import _full_name

PERSPECTIVE_ALTER_LIMIT = 40


@query.field("perspective")
def resolve_perspective(_obj, info, personId: str) -> dict[str, Any]:
    row_id = _decode_id(personId, "person")
    session = _session(info)
    on = date.today()

    total = int(
        session.execute(
            text(
                "SELECT count(*) FROM publication_authors "
                "WHERE person_id = :pid"
            ),
            {"pid": row_id},
        ).scalar_one()
    )

    # Internal working dictionary is keyed by database row id so we never
    # decode the public id we just encoded; only the returned GraphQL shape
    # carries public ids.
    alters: dict[int, dict[str, Any]] = {}
    rows = session.execute(
        text(
            """
            SELECT
              CASE WHEN e.person_a = :pid THEN e.person_b
                   ELSE e.person_a END AS other_id,
              e.paper_count
            FROM person_coauthor_edges e
            WHERE e.person_a = :pid OR e.person_b = :pid
            """
        ),
        {"pid": row_id},
    ).all()
    for other_id, count in rows:
        other_id = int(other_id)
        importance = float(count) / total if total else 0.0
        alters[other_id] = {
            "_row_id": other_id,
            "personId": encode("person", other_id),
            "paperCount": int(count),
            "importance": min(1.0, importance),
            "group": 0,
            "hop": 1,
            "relation": "coauthor",
        }

    # Advisor/advisee relationships active today.  If someone is both an
    # advisor and a coauthor, keep the coauthor paper count and enrich the
    # relation label rather than creating a second alter.
    for rel in _person_relations_active(session, row_id, "advised_by", on):
        other = int(
            rel.to_person_id
            if rel.from_person_id == row_id
            else rel.from_person_id
        )
        entry = alters.setdefault(
            other,
            {
                "_row_id": other,
                "personId": encode("person", other),
                "paperCount": 0,
                "importance": 0.0,
                "group": 0,
                "hop": 1,
                "relation": None,
            },
        )
        entry["relation"] = (
            "advisor" if rel.from_person_id == row_id else "advisee"
        )
        entry["importance"] = max(entry["importance"], 0.55)

    # hop-2: shared-coauthor-only alters.  For each edge touching at least
    # one of my direct coauthors, the other endpoint is a candidate; a
    # candidate needs two or more distinct shared coauthors to matter.
    rows2 = session.execute(
        text(
            """
            WITH me AS (
              SELECT
                CASE WHEN person_a = :pid THEN person_b
                     ELSE person_a END AS other
              FROM person_coauthor_edges
              WHERE person_a = :pid OR person_b = :pid
            ),
            candidate_edges AS (
              SELECT
                e.person_a,
                e.person_b,
                m.other AS shared_coauthor,
                CASE WHEN e.person_a = m.other THEN e.person_b
                     ELSE e.person_a END AS other_id
              FROM person_coauthor_edges e
              JOIN me m ON m.other IN (e.person_a, e.person_b)
              WHERE e.person_a <> :pid
                AND e.person_b <> :pid
            )
            SELECT other_id, count(DISTINCT shared_coauthor) AS shared_count
            FROM candidate_edges
            WHERE other_id <> :pid
            GROUP BY other_id
            HAVING count(DISTINCT shared_coauthor) >= 2
            """
        ),
        {"pid": row_id},
    ).all()
    for other_id, shared_count in rows2:
        other_id = int(other_id)
        if other_id in alters:
            continue
        importance = min(0.35 * (float(shared_count) / 10.0), 0.35)
        alters[other_id] = {
            "_row_id": other_id,
            "personId": encode("person", other_id),
            "paperCount": 0,
            "importance": importance,
            "group": 0,
            "hop": 2,
            "relation": None,
        }

    ordered = sorted(
        alters.values(), key=lambda a: (-a["importance"], a["_row_id"])
    )[:PERSPECTIVE_ALTER_LIMIT]
    alter_ids = [int(a["_row_id"]) for a in ordered]

    groups = _alter_groups(session, alter_ids)
    # Batch briefs: up to 40 alters in ~4 queries instead of ~120.
    briefs = _person_briefs(session, alter_ids, on)
    for a, pid in zip(ordered, alter_ids):
        a.pop("_row_id", None)
        a["group"] = groups.get(pid, 0)
        brief = briefs.get(pid)
        if brief:
            person = brief["person"]
            a["label"] = _full_name(
                person.firstname, person.middlename, person.lastname
            )
            a["institution"] = brief["institution"]
            a["rank"] = brief["rank"]
        else:
            a["label"] = "?"
            a["institution"] = None
            a["rank"] = None

    max_paper = max((a["paperCount"] or 0) for a in ordered) if ordered else 0
    return {
        "focusId": personId,
        "alterCount": len(ordered),
        "maxPaperCount": max_paper,
        "alters": ordered,
    }
