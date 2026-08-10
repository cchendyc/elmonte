"""Parse ORCID employment and education into affiliation candidates."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from scripts.backfill.common import CvAffiliationCandidate
from scripts.backfill.rank import classify_position_rank


def parse_orcid_record(record: dict[str, Any]) -> list[CvAffiliationCandidate]:
    summary = record.get("activities-summary") or {}
    entries: list[CvAffiliationCandidate] = []

    for group in (summary.get("employments") or {}).get("affiliation-group") or []:
        for item in group.get("summaries") or []:
            employment = item.get("employment-summary")
            if employment:
                candidate = _parse_affiliation(employment, affiliation_kind="employment")
                if candidate:
                    entries.append(candidate)

    for group in (summary.get("educations") or {}).get("affiliation-group") or []:
        for item in group.get("summaries") or []:
            education = item.get("education-summary")
            if education:
                candidate = _parse_affiliation(education, affiliation_kind="education")
                if candidate:
                    entries.append(candidate)

    return entries


def _parse_affiliation(
    item: dict[str, Any], *, affiliation_kind: str
) -> CvAffiliationCandidate | None:
    org = item.get("organization") or {}
    org_name = (org.get("name") or "").strip()
    department = (item.get("department-name") or "").strip()
    role = (item.get("role-title") or "").strip()

    if affiliation_kind == "education" and not role:
        role = department or "Degree"
    if not role:
        role = department or org_name
    if not role:
        return None

    organization = org_name
    if department and department.lower() not in role.lower():
        organization = f"{org_name}, {department}" if org_name else department

    starts_at = _partial_date(item.get("start-date"))
    ends_at = _partial_date(item.get("end-date"))

    return CvAffiliationCandidate(
        title=role,
        organization=organization,
        affiliation_kind=affiliation_kind,
        position_rank=classify_position_rank(role),
        starts_at=starts_at,
        ends_at=ends_at,
    )


def _partial_date(raw: dict[str, Any] | None) -> datetime | None:
    if not raw:
        return None
    year = _int_value(raw.get("year"))
    if year is None:
        return None
    month = _int_value(raw.get("month")) or 1
    day = _int_value(raw.get("day")) or 1
    month = max(1, min(month, 12))
    day = max(1, min(day, 28))
    return datetime(year, month, day, tzinfo=timezone.utc)


def _int_value(node: Any) -> int | None:
    if not isinstance(node, dict):
        return None
    value = node.get("value")
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None
