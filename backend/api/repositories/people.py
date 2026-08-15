from __future__ import annotations

"""Person data access: anchors, briefs, publications, relations, closest people.

SQL-first module.  The brief builders stay here (single-query compositors over
people/anchors/orgs); the heavier profile *orchestration* lives in
api/services/person.py.  Depends only on repositories.orgs + the pure
services.names helpers, so there are no import cycles.
"""

from datetime import date, timedelta
from typing import Any

from api.id_codec import encode
from api.repositories.orgs import _institution_of
from api.services.names import _full_name, _person_role
from db.models import Person, PersonRelationship
from scripts.backfill.common import is_displayable_publication
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

COLLEAGUE_LIMIT = 8

def _anchor_of(
    session: Session,
    person_id: int,
    on: date,
    *,
    include_historical: bool = False,
) -> dict[str, Any] | None:
    """Chart anchor for `person_id` as of `on`.

    When `include_historical` is true and no row is active on `on`, fall back
    to the most recent chart anchor so retired coauthors still have a home org.
    """
    temporal = "" if include_historical else "AND pa.validity @> :as_of"
    row = session.execute(
        text(
            f"""
            SELECT
              pa.affiliation_id,
              pa.title,
              pa.position_rank,
              pa.is_primary,
              pa.organization_id,
              pa.validity,
              paf.ends_at
            FROM person_anchor pa
            JOIN person_affiliations paf ON paf.id = pa.affiliation_id
            WHERE pa.person_id = :pid
              {temporal}
            ORDER BY
              CASE WHEN pa.validity @> :as_of THEN 0 ELSE 1 END,
              pa.is_primary DESC,
              upper(pa.validity) DESC NULLS FIRST,
              paf.starts_at DESC NULLS LAST
            LIMIT 1
            """
        ),
        {"pid": person_id, "as_of": on},
    ).mappings().first()
    return dict(row) if row else None


def _retired_at(anchor: dict[str, Any] | None, on: date) -> date | None:
    """When the person left the chart roster — not when they stopped publishing."""
    if anchor is None:
        return None
    validity = anchor.get("validity")
    if validity is not None and on in validity:
        return None
    ends_at = anchor.get("ends_at")
    if ends_at is not None:
        return ends_at.date() if hasattr(ends_at, "date") else ends_at
    if validity is not None:
        upper = validity.upper
        if upper is not None and not getattr(upper, "inf", False):
            return upper - timedelta(days=1)
    return None


def _anchor_context(
    session: Session,
    person_id: int,
    on: date,
) -> tuple[dict[str, Any] | None, date | None]:
    anchor = _anchor_of(session, person_id, on)
    if anchor is None:
        anchor = _anchor_of(session, person_id, on, include_historical=True)
    return anchor, _retired_at(anchor, on)
def _top_coauthors(session: Session, person_id: int, limit: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
              CASE WHEN person_a = :pid THEN person_b ELSE person_a END AS other_id,
              paper_count
            FROM person_coauthor_edges
            WHERE person_a = :pid OR person_b = :pid
            ORDER BY paper_count DESC
            LIMIT :lim
            """
        ),
        {"pid": person_id, "lim": limit},
    ).mappings().all()
    return [dict(r) for r in rows]
def _person_brief(
    session: Session,
    person_id: int,
    on: date,
) -> dict[str, Any] | None:
    person = session.get(Person, person_id)
    if person is None:
        return None
    anchor, retired_at = _anchor_context(session, person_id, on)
    inst_name: str | None = None
    if anchor and anchor.get("organization_id"):
        inst = _institution_of(session, anchor["organization_id"], on)
        inst_name = inst.name if inst else None
    return {
        "person": person,
        "anchor": anchor,
        "institution": inst_name,
        "role": _person_role(anchor.get("title") if anchor else None),
        "rank": anchor.get("position_rank") if anchor else None,
        "retiredAt": retired_at,
    }
def _person_briefs(
    session: Session,
    person_ids: list[int],
    on: date,
) -> dict[int, dict[str, Any]]:
    """Batch :func:`_person_brief` for many ids.

    The per-person version costs ~3 queries per person (person row, anchor,
    institution walk) — 120+ queries for a 40-alter perspective.  This does
    the same work in a constant ~4 queries: one person lookup, one
    ``DISTINCT ON`` anchor lookup (which reproduces ``_anchor_of``'s
    ordering, including the historical fallback), then a batched parent walk
    for institutions.
    """
    ids = list(dict.fromkeys(person_ids))
    if not ids:
        return {}

    persons = {
        p.id: p
        for p in session.execute(select(Person).where(Person.id.in_(ids))).scalars()
    }
    if not persons:
        return {}

    # One DISTINCT ON query: per-person top-1 under the same ordering as
    # _anchor_of — active anchors first, then newest historical.  All ORDER BY
    # expressions are in the select list (required with DISTINCT).
    anchor_rows = session.execute(
        text(
            """
            SELECT DISTINCT ON (pa.person_id)
              pa.person_id,
              pa.affiliation_id,
              pa.title,
              pa.position_rank,
              pa.is_primary,
              pa.organization_id,
              pa.validity,
              paf.ends_at,
              CASE WHEN pa.validity @> :as_of THEN 0 ELSE 1 END AS temporal_rank,
              upper(pa.validity) AS validity_upper,
              paf.starts_at
            FROM person_anchor pa
            JOIN person_affiliations paf ON paf.id = pa.affiliation_id
            WHERE pa.person_id = ANY(:ids)
            ORDER BY
              pa.person_id,
              temporal_rank,
              pa.is_primary DESC,
              validity_upper DESC NULLS FIRST,
              paf.starts_at DESC NULLS LAST
            """
        ),
        {"ids": list(persons), "as_of": on},
    ).mappings().all()
    anchors = {int(r["person_id"]): dict(r) for r in anchor_rows}

    # Institutions: batch-walk the parent chain to the university ancestor
    # (mirrors _institution_of's depth cap of 8) in a constant number of
    # queries instead of one round-trip per person.  `origin` remembers which
    # anchor org each chain started from, so a university found for a parent
    # resolves every descendant that led there — mirroring _institution_of,
    # which returns the university reached *from the anchor org*.
    inst_by_org: dict[int, str | None] = {}
    anchor_org_ids = [
        int(a["organization_id"])
        for a in anchors.values()
        if a.get("organization_id") is not None
    ]
    if anchor_org_ids:
        org_info: dict[int, tuple[str, str]] = {}
        rows = session.execute(
            text("SELECT id, name, kind FROM organizations WHERE id = ANY(:ids)"),
            {"ids": list(dict.fromkeys(anchor_org_ids))},
        ).mappings().all()
        org_info.update({int(r["id"]): (r["name"], r["kind"]) for r in rows})

        pending = list(dict.fromkeys(anchor_org_ids))
        origin: dict[int, list[int]] = {oid: [oid] for oid in pending}
        for _ in range(8):
            unresolved: list[int] = []
            for oid in pending:
                info = org_info.get(oid)
                if info is not None and info[1] == "university":
                    for src in origin[oid]:
                        inst_by_org[src] = info[0]
                else:
                    unresolved.append(oid)
            if not unresolved:
                break
            parent_rows = session.execute(
                text(
                    """
                    SELECT r.child_org_id, o.id AS parent_id, o.name, o.kind
                    FROM org_relationships r
                    JOIN organizations o ON o.id = r.parent_org_id
                    WHERE r.relationship_type = 'primary'
                      AND r.child_org_id = ANY(:ids)
                      AND r.validity @> :as_of
                    """
                ),
                {"ids": unresolved, "as_of": on},
            ).mappings().all()
            parents: dict[int, tuple[int, str, str]] = {
                int(r["child_org_id"]): (int(r["parent_id"]), r["name"], r["kind"])
                for r in parent_rows
            }
            merged: dict[int, list[int]] = {}
            for oid in unresolved:
                parent = parents.get(oid)
                if parent is None:  # no parent → no university ancestor
                    for src in origin[oid]:
                        inst_by_org[src] = None
                    continue
                pid, pname, pkind = parent
                if pid == oid:  # degenerate self-parent — never resolves
                    for src in origin[oid]:
                        inst_by_org[src] = None
                    continue
                org_info.setdefault(pid, (pname, pkind))
                merged.setdefault(pid, []).extend(origin[oid])
            pending = list(merged)
            origin = merged
        for srcs in origin.values():  # depth cap reached — unresolved
            for src in srcs:
                inst_by_org.setdefault(src, None)

    result: dict[int, dict[str, Any]] = {}
    for pid, person in persons.items():
        anchor = anchors.get(pid)
        inst_name: str | None = None
        if anchor and anchor.get("organization_id"):
            inst_name = inst_by_org.get(int(anchor["organization_id"]))
        result[pid] = {
            "person": person,
            "anchor": anchor,
            "institution": inst_name,
            "role": _person_role(anchor.get("title") if anchor else None),
            "rank": anchor.get("position_rank") if anchor else None,
            "retiredAt": _retired_at(anchor, on),
        }
    return result
def _colleagues_at_anchor(
    session: Session,
    person_id: int,
    on: date,
    limit: int,
) -> list[dict[str, Any]]:
    anchor = _anchor_of(session, person_id, on)
    if not anchor or not anchor.get("organization_id"):
        return []
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
              AND r.person_id <> :pid
              AND r.validity @> :as_of
            ORDER BY r.sort_key, r.person_id
            LIMIT :lim
            """
        ),
        {
            "oid": anchor["organization_id"],
            "pid": person_id,
            "as_of": on,
            "lim": limit,
        },
    ).mappings().all()
    return [dict(r) for r in rows]
def _person_publications(
    session: Session, person_id: int, limit: int | None
) -> list[dict[str, Any]]:
    """Return displayable publications for *person_id*.

    ``limit=None`` means no upper bound (GDPR Art. 20 export must be complete).
    The SQL limit is a best-effort pre-filter; junk titles are filtered again
    in Python below, so callers needing an exact page size should request a
    little headroom.
    """
    if limit is None:
        sql = """
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
            """
        params: dict[str, Any] = {"pid": person_id}
    else:
        sql = """
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
            LIMIT :lim
            """
        params = {"pid": person_id, "lim": limit}

    rows = session.execute(text(sql), params).mappings().all()

    pubs = [
        {
            "id": f"pub:{int(row['id'])}",
            "rowId": int(row["id"]),
            "title": row["title"],
            "year": int(row["publication_year"]),
            "citedByCount": int(row["cited_by_count"]) if row["cited_by_count"] is not None else None,
            "authorPosition": int(row["author_position"]),
        }
        for row in rows
        if is_displayable_publication(row["title"])
    ]
    if not pubs:
        return []

    pub_ids = [pub["rowId"] for pub in pubs]

    doi_rows = session.execute(
        text(
            """
            SELECT publication_id, external_id
            FROM external_identifiers
            WHERE provider = 'doi' AND publication_id = ANY(:ids)
            """
        ),
        {"ids": pub_ids},
    ).mappings().all()
    doi_by_pub = {int(r["publication_id"]): r["external_id"] for r in doi_rows}

    venue_rows = session.execute(
        text(
            """
            SELECT p.id AS publication_id, o.short_name, o.name
            FROM publications p
            LEFT JOIN organizations o ON o.id = p.venue_org_id
            WHERE p.id = ANY(:ids)
            """
        ),
        {"ids": pub_ids},
    ).mappings().all()
    venue_by_pub = {
        int(r["publication_id"]): r["short_name"] or r["name"]
        for r in venue_rows
    }

    for pub in pubs:
        pub_id = pub.pop("rowId")
        pub["doi"] = doi_by_pub.get(pub_id)
        pub["venue"] = venue_by_pub.get(pub_id)
    return pubs
def _person_relations_all(
    session: Session, person_id: int, kind: str
) -> list[PersonRelationship]:
    return list(
        session.execute(
            select(PersonRelationship).where(
                PersonRelationship.type == kind,
                or_(
                    PersonRelationship.from_person_id == person_id,
                    PersonRelationship.to_person_id == person_id,
                ),
            )
        ).scalars()
    )
def _closest_people(session: Session, person_id: int, on: date, limit: int) -> list[dict[str, Any]]:
    seen: set[int] = {person_id}
    results: list[dict[str, Any]] = []

    # Batch-load briefs for every candidate that needs one (advisors/advisees
    # and coauthors) in ~3 queries total instead of one per person.
    relations = _person_relations_all(session, person_id, "advised_by")
    brief_ids: list[int] = [
        rel.to_person_id if rel.from_person_id == person_id else rel.from_person_id
        for rel in relations
    ]
    coauthor_rows = _top_coauthors(session, person_id, limit)
    colleague_rows = _colleagues_at_anchor(session, person_id, on, COLLEAGUE_LIMIT)
    brief_ids.extend(int(row["other_id"]) for row in coauthor_rows)
    brief_ids.extend(int(row["person_id"]) for row in colleague_rows)
    briefs = _person_briefs(session, brief_ids, on)

    def add_hit(
        other_id: int,
        relation: str,
        detail: str | None,
        priority: int,
        *,
        label: str | None = None,
        role: str | None = None,
        institution: str | None = None,
    ) -> None:
        if other_id in seen:
            return
        if label is None:
            brief = briefs.get(other_id)
            if brief is None:
                return
            person = brief["person"]
            label = _full_name(person.firstname, person.middlename, person.lastname)
            role = brief["role"]
            institution = brief["institution"]
        seen.add(other_id)
        results.append(
            {
                "id": encode("person", other_id),
                "label": label,
                "role": role,
                "institution": institution,
                "relation": relation,
                "detail": detail,
                "_priority": priority,
            }
        )

    for rel in relations:
        if rel.from_person_id == person_id:
            add_hit(rel.to_person_id, "advisor", None, 0)
        else:
            add_hit(rel.from_person_id, "advisee", None, 1)

    for row in coauthor_rows:
        weight = int(row["paper_count"])
        add_hit(
            row["other_id"],
            "coauthor",
            f"{weight} paper" + ("" if weight == 1 else "s"),
            10 - min(weight, 9),
        )

    for row in colleague_rows:
        add_hit(row["person_id"], "colleague", "Same unit", 20)

    results.sort(key=lambda item: (item["_priority"], item["label"]))
    for item in results:
        item.pop("_priority", None)
    return results[:limit]
def _person_relations_active(
    session: Session, person_id: int, kind: str, on: date
) -> list[PersonRelationship]:
    """Directed relationship rows whose validity contains ``on``."""
    return list(
        session.execute(
            select(PersonRelationship).where(
                PersonRelationship.type == kind,
                or_(
                    PersonRelationship.from_person_id == person_id,
                    PersonRelationship.to_person_id == person_id,
                ),
                text("person_relationships.validity @> :as_of"),
            ).params(as_of=on)
        ).scalars()
    )


# ---------------------------------------------------------------------------
# expand: person and org branches
# ---------------------------------------------------------------------------
