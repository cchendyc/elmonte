from __future__ import annotations

"""Person-profile services: career timeline + full profile assembly.

Pure orchestration over repositories (no cycles: nothing in this module is
imported by api.repositories).
"""

from datetime import date
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

from api.id_codec import encode
from api.repositories.people import _closest_people, _person_brief, _person_publications
from api.services.names import _full_name, _person_role

CLOSEST_PEOPLE_LIMIT = 16
PUBLICATION_LIMIT = 40

def _career_timeline(session: Session, person_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
              pa.title,
              pa.affiliation_kind,
              pa.position_rank,
              pa.is_primary,
              pa.starts_at,
              pa.ends_at,
              org.name       AS org_name,
              org.short_name AS org_short_name
            FROM person_affiliations pa
            LEFT JOIN LATERAL (
              SELECT o.name, o.short_name
              FROM affiliation_org_assignments aoa
              JOIN organizations o ON o.id = aoa.organization_id
              WHERE aoa.affiliation_id = pa.id
              ORDER BY
                CASE aoa.assignment_type
                  WHEN 'chart_anchor' THEN 0
                  ELSE 1
                END,
                aoa.organization_id
              LIMIT 1
            ) org ON TRUE
            WHERE pa.person_id = :pid
            ORDER BY pa.starts_at DESC NULLS LAST, pa.id DESC
            """
        ),
        {"pid": person_id},
    ).mappings().all()
    entries: list[dict[str, Any]] = []
    for row in rows:
        org = row["org_short_name"] or row["org_name"] or "Organization not linked"
        starts = row["starts_at"].date() if row["starts_at"] else None
        ends = row["ends_at"].date() if row["ends_at"] else None
        entries.append(
            {
                "title": _person_role(row["title"]),
                "organization": org,
                "affiliationKind": row["affiliation_kind"],
                "positionRank": row["position_rank"],
                "isPrimary": bool(row["is_primary"]),
                "startsAt": starts,
                "endsAt": ends,
            }
        )
    return entries
def _resolve_person(session: Session, person_id: int, on: date) -> dict[str, Any] | None:
    brief = _person_brief(session, person_id, on)
    if brief is None:
        return None
    person = brief["person"]

    # ORCID from external_identifiers
    orcid_row = session.execute(
        text(
            "SELECT external_id FROM external_identifiers "
            "WHERE person_id = :pid AND provider = 'orcid' "
            "LIMIT 1"
        ),
        {"pid": person_id},
    ).mappings().first()
    orcid: str | None = orcid_row["external_id"] if orcid_row else None

    return {
        "id": encode("person", person.id),
        "label": _full_name(person.firstname, person.middlename, person.lastname),
        "role": brief["role"],
        "institution": brief["institution"],
        "biography": person.biography,
        "homepageUrl": person.homepage_url,
        "cvUrl": (
            f"/api/people/{encode('person', person.id)}/cv"
            if person.cv_snapshot_id
            else None
        ),
        "orcid": orcid,
        "careerTimeline": _career_timeline(session, person_id),
        "publications": _person_publications(session, person_id, PUBLICATION_LIMIT),
        "closestPeople": _closest_people(session, person_id, on, CLOSEST_PEOPLE_LIMIT),
    }
