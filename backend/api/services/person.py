from __future__ import annotations

"""Person-profile services: career timeline + full profile assembly.

Pure orchestration over repositories (no cycles: nothing in this module is
imported by api.repositories).
"""

from datetime import date, datetime
from typing import Any

from api.id_codec import encode
from api.repositories.people import _closest_people, _person_brief, _person_publications
from api.services.evidence import evidence_sources
from api.services.names import _full_name, _person_role
from sqlalchemy import text
from sqlalchemy.orm import Session

CLOSEST_PEOPLE_LIMIT = 16
# Profile cards show the complete bibliography for every current researcher
# (max in the live corpus is <70); the GDPR export path is unbounded.
PUBLICATION_LIMIT = 100


def _date_only(value: datetime | date | None) -> date | None:
    """Date scalar output — psycopg returns timestamptz for several columns."""
    if value is None:
        return None
    return value.date() if isinstance(value, datetime) else value


def _person_topics(session: Session, person_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT t.display_name, pt.score, pt.works_count
            FROM person_topics pt
            JOIN topics t ON t.openalex_topic_id = pt.topic_id
            WHERE pt.person_id = :pid
            ORDER BY pt.score DESC, t.display_name
            """
        ),
        {"pid": person_id},
    ).mappings().all()
    return [
        {
            "displayName": row["display_name"],
            "score": float(row["score"]),
            "worksCount": int(row["works_count"]),
        }
        for row in rows
    ]


def _person_concepts(session: Session, person_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT c.display_name, pc.score, pc.rank
            FROM person_concepts pc
            JOIN concepts c ON c.id = pc.concept_id
            WHERE pc.person_id = :pid
            ORDER BY pc.rank ASC NULLS LAST, c.display_name
            """
        ),
        {"pid": person_id},
    ).mappings().all()
    return [
        {
            "displayName": row["display_name"],
            "score": float(row["score"]) if row["score"] is not None else None,
            "rank": int(row["rank"]) if row["rank"] is not None else None,
        }
        for row in rows
    ]


def _person_awards(session: Session, person_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT pa.id AS conferral_id, pa.awarded_at, pa.verification_status,
                   a.name
            FROM person_awards pa
            JOIN awards a ON a.id = pa.award_id
            WHERE pa.person_id = :pid
            ORDER BY pa.awarded_at DESC NULLS LAST, a.name, pa.id
            """
        ),
        {"pid": person_id},
    ).mappings().all()
    sources = evidence_sources(
        session,
        subject_column="person_award_id",
        subject_ids=[int(row["conferral_id"]) for row in rows],
    )
    return [
        {
            "name": row["name"],
            "awardedAt": _date_only(row["awarded_at"]),
            "verificationStatus": row["verification_status"],
            "sources": sources.get(int(row["conferral_id"]), []),
        }
        for row in rows
    ]


def _person_grants(session: Session, person_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
              g.id AS grant_id,
              g.title,
              g.award_number,
              g.amount,
              g.currency,
              g.starts_at,
              g.ends_at,
              g.verification_status,
              gp.role,
              funder.short_name AS funder_short_name,
              funder.name AS funder_name
            FROM grant_participants gp
            JOIN grants g ON g.id = gp.grant_id
            LEFT JOIN organizations funder ON funder.id = g.funder_org_id
            WHERE gp.person_id = :pid
            ORDER BY g.starts_at DESC NULLS LAST, g.title, g.id
            """
        ),
        {"pid": person_id},
    ).mappings().all()
    sources = evidence_sources(
        session,
        subject_column="grant_id",
        subject_ids=[int(row["grant_id"]) for row in rows],
    )
    return [
        {
            "title": row["title"],
            "funder": row["funder_short_name"] or row["funder_name"] or "Unknown funder",
            "role": row["role"],
            "awardNumber": row["award_number"],
            "amount": float(row["amount"]) if row["amount"] is not None else None,
            "currency": row["currency"].strip() if row["currency"] else None,
            "startsAt": _date_only(row["starts_at"]),
            "endsAt": _date_only(row["ends_at"]),
            "verificationStatus": row["verification_status"],
            "sources": sources.get(int(row["grant_id"]), []),
        }
        for row in rows
    ]


def _person_relationships_export(
    session: Session, person_id: int
) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
              pr.id,
              pr.type,
              pr.starts_at,
              pr.ends_at,
              pr.verification_status,
              CASE WHEN pr.from_person_id = :pid
                   THEN pr.to_person_id
                   ELSE pr.from_person_id
              END AS other_id,
              other.firstname,
              other.middlename,
              other.lastname
            FROM person_relationships pr
            JOIN people other
              ON other.id = CASE WHEN pr.from_person_id = :pid
                                THEN pr.to_person_id
                                ELSE pr.from_person_id
                           END
            WHERE pr.from_person_id = :pid OR pr.to_person_id = :pid
            ORDER BY pr.starts_at DESC NULLS LAST, pr.id
            """
        ),
        {"pid": person_id},
    ).mappings().all()
    sources = evidence_sources(
        session,
        subject_column="person_relationship_id",
        subject_ids=[int(row["id"]) for row in rows],
    )
    return [
        {
            "type": row["type"],
            "otherPersonId": encode("person", int(row["other_id"])),
            "otherPersonLabel": _full_name(
                row["firstname"], row["middlename"], row["lastname"]
            ),
            "startsAt": _date_only(row["starts_at"]),
            "endsAt": _date_only(row["ends_at"]),
            "verificationStatus": row["verification_status"],
            "sources": sources.get(int(row["id"]), []),
        }
        for row in rows
    ]


def _career_timeline(session: Session, person_id: int) -> list[dict[str, Any]]:
    rows = session.execute(
        text(
            """
            SELECT
              pa.id,
              pa.title,
              pa.affiliation_kind,
              pa.position_rank,
              pa.is_primary,
              pa.starts_at,
              pa.ends_at,
              pa.verification_status,
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
    sources = evidence_sources(
        session,
        subject_column="affiliation_id",
        subject_ids=[int(row["id"]) for row in rows],
    )
    entries: list[dict[str, Any]] = []
    for row in rows:
        org = row["org_short_name"] or row["org_name"] or "Organization not linked"
        entries.append(
            {
                "title": _person_role(row["title"]),
                "organization": org,
                "affiliationKind": row["affiliation_kind"],
                "positionRank": row["position_rank"],
                "isPrimary": bool(row["is_primary"]),
                "startsAt": _date_only(row["starts_at"]),
                "endsAt": _date_only(row["ends_at"]),
                "verificationStatus": row["verification_status"],
                "sources": sources.get(int(row["id"]), []),
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
        "awards": _person_awards(session, person_id),
        "grants": _person_grants(session, person_id),
        "personTopics": _person_topics(session, person_id),
        "personConcepts": _person_concepts(session, person_id),
    }
