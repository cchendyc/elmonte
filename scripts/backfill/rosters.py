"""Ingest economics faculty rosters into org trees + people.

Creates university → school → department org trees, scrapes official faculty
directories, and links or creates `people` rows with chart-anchored affiliations
and `official_url` identifiers. Existing people are matched by profile URL or
unique normalized name, then missing affiliation / URL rows are filled.

    .venv/bin/python -m scripts.backfill.rosters --dry-run
    .venv/bin/python -m scripts.backfill.rosters
    .venv/bin/python -m scripts.backfill.rosters --schools mit,yale --fill-profiles

Harvard economics blocks automated directory access (HTTP 403); ingest it
manually or from snapshots when available.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.parse
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal
from scripts.backfill.common import (
    PoliteFetcher,
    upsert_external_identifier,
    write_snapshot,
)
from scripts.backfill.rank import classify_position_rank, infer_affiliation_kind

Fetcher = PoliteFetcher


@dataclass(frozen=True)
class OrgSpec:
    university: tuple[str, str | None]
    school: tuple[str, str | None] | None
    department: tuple[str, str | None]


@dataclass(frozen=True)
class RosterEntry:
    display_name: str
    title: str | None
    profile_url: str | None


@dataclass(frozen=True)
class SchoolRoster:
    key: str
    org: OrgSpec
    roster_url: str
    fetch_entries: Callable[[Fetcher], list[RosterEntry]]


def _split_name(display_name: str) -> tuple[str, str | None, str]:
    cleaned = " ".join(display_name.strip().split())
    parts = cleaned.split()
    if not parts:
        raise ValueError("blank display name")
    if len(parts) == 1:
        return parts[0], None, parts[0]
    if len(parts) == 2:
        return parts[0], None, parts[1]
    return parts[0], " ".join(parts[1:-1]), parts[-1]


def _normalize_name_key(first: str, last: str) -> str:
    return f"{first.strip().lower()}::{last.strip().lower()}"


def _absolute_url(base: str, href: str) -> str:
    return urllib.parse.urljoin(base, href)


def _fetch_json(fetcher: Fetcher, url: str) -> dict:
    status, body, _ = fetcher.fetch(url, accept="application/vnd.api+json")
    if status >= 400:
        raise RuntimeError(f"{url} returned HTTP {status}")
    return json.loads(body.decode("utf-8"))


def _parse_princeton_people(html: str) -> list[RosterEntry]:
    entries: list[RosterEntry] = []
    for block in re.findall(
        r'<div class="person">(.*?)</div>\s*</div>\s*</div>', html, re.S
    ):
        name_m = re.search(r"<h3>([^<]+)</h3>", block)
        if not name_m:
            continue
        title_m = re.search(r'<p class="job">([^<]+)</p>', block)
        link_m = re.search(r'<a[^>]+href="([^"]+)"', block)
        entries.append(
            RosterEntry(
                display_name=name_m.group(1).strip(),
                title=title_m.group(1).strip() if title_m else None,
                profile_url=link_m.group(1).strip() if link_m else None,
            )
        )
    return entries


def _fetch_mit(fetcher: Fetcher) -> list[RosterEntry]:
    status, body, _ = fetcher.fetch("https://economics.mit.edu/people/faculty")
    if status >= 400:
        raise RuntimeError(f"MIT faculty directory HTTP {status}")
    html = body.decode("utf-8", errors="replace")
    entries: list[RosterEntry] = []
    for slug in sorted(set(re.findall(r'href="/people/faculty/([a-z0-9\-]+)/?"', html))):
        profile_url = f"https://economics.mit.edu/people/faculty/{slug}"
        status, body, _ = fetcher.fetch(profile_url)
        if status >= 400:
            continue
        page = body.decode("utf-8", errors="replace")
        title_m = re.search(
            r'class="hs-font-lead field-hs-person-title[^"]*">.*?<div>\s*([^<]+?)\s*</div>',
            page,
            re.S | re.I,
        )
        name_m = re.search(r"<title>([^|<]+)", page)
        display = name_m.group(1).strip() if name_m else slug.replace("-", " ").title()
        entries.append(
            RosterEntry(
                display_name=display,
                title=title_m.group(1).strip() if title_m else None,
                profile_url=profile_url,
            )
        )
    return entries


def _fetch_yale(fetcher: Fetcher) -> list[RosterEntry]:
    base = "https://egc.yale.edu"
    skip = {
        "faculty",
        "graduate-students",
        "staff",
        "egc-alumni",
        "egc-visiting-faculty",
        "postdoctoral-scholars",
        "student-interns",
        "people",
    }
    slugs: list[str] = []
    for page in range(0, 20):
        status, body, _ = fetcher.fetch(f"{base}/people/faculty?page={page}")
        if status >= 400:
            raise RuntimeError(f"Yale faculty directory HTTP {status}")
        html = body.decode("utf-8", errors="replace")
        page_slugs = [
            slug
            for slug in dict.fromkeys(
                re.findall(r'href="/people/([a-z0-9\-]+)/?"', html)
            )
            if slug not in skip
        ]
        if not page_slugs:
            break
        slugs.extend(page_slugs)
    slugs = list(dict.fromkeys(slugs))
    entries: list[RosterEntry] = []
    for slug in slugs:
        profile_url = f"{base}/people/{slug}"
        status, body, _ = fetcher.fetch(profile_url)
        if status >= 400:
            continue
        page = body.decode("utf-8", errors="replace")
        title_m = re.search(
            r'class="hs-font-lead field-hs-person-title[^"]*">.*?<div>\s*([^<]+?)\s*</div>',
            page,
            re.S | re.I,
        )
        name_m = re.search(r"<title>([^|<]+)", page)
        display = name_m.group(1).strip() if name_m else slug.replace("-", " ").title()
        entries.append(
            RosterEntry(
                display_name=display,
                title=title_m.group(1).strip() if title_m else None,
                profile_url=profile_url,
            )
        )
    return entries


def _fetch_uchicago(fetcher: Fetcher) -> list[RosterEntry]:
    base = "https://economics.uchicago.edu"
    person_types = ("Faculty", "Associated Faculty (Economics)")
    by_url: dict[str, RosterEntry] = {}
    for person_type in person_types:
        offset = 0
        while True:
            query = urllib.parse.urlencode(
                {
                    "filter[field_person_type.name]": person_type,
                    "page[limit]": 50,
                    "page[offset]": offset,
                }
            )
            data = _fetch_json(fetcher, f"{base}/jsonapi/node/person?{query}")
            batch = data.get("data") or []
            if not batch:
                break
            for item in batch:
                attrs = item.get("attributes") or {}
                title = (attrs.get("title") or "").strip()
                if not title:
                    continue
                path = attrs.get("path") or {}
                alias = path.get("alias")
                profile_url = f"{base}{alias}" if alias else None
                if profile_url and profile_url in by_url:
                    continue
                rank_title = person_type if person_type != "Faculty" else None
                by_url[profile_url or title] = RosterEntry(
                    display_name=title,
                    title=rank_title,
                    profile_url=profile_url,
                )
            offset += len(batch)
            if len(batch) < 50:
                break
    return list(by_url.values())


def _fetch_princeton(fetcher: Fetcher) -> list[RosterEntry]:
    status, body, _ = fetcher.fetch("https://economics.princeton.edu/people/")
    if status >= 400:
        raise RuntimeError(f"Princeton faculty directory HTTP {status}")
    html = body.decode("utf-8", errors="replace")
    return _parse_princeton_people(html)


SCHOOLS: dict[str, SchoolRoster] = {
    "mit": SchoolRoster(
        key="mit",
        org=OrgSpec(
            university=("Massachusetts Institute of Technology", "MIT"),
            school=(
                "MIT School of Humanities, Arts, and Social Sciences",
                "MIT SHASS",
            ),
            department=("MIT Department of Economics", "MIT Economics"),
        ),
        roster_url="https://economics.mit.edu/people/faculty",
        fetch_entries=_fetch_mit,
    ),
    "yale": SchoolRoster(
        key="yale",
        org=OrgSpec(
            university=("Yale University", "Yale"),
            school=("Yale Faculty of Arts and Sciences", "Yale FAS"),
            department=("Yale Department of Economics", "Yale Economics"),
        ),
        roster_url="https://egc.yale.edu/people/faculty",
        fetch_entries=_fetch_yale,
    ),
    "uchicago": SchoolRoster(
        key="uchicago",
        org=OrgSpec(
            university=("University of Chicago", "UChicago"),
            school=("University of Chicago Division of the Social Sciences", "UChicago SSD"),
            department=(
                "University of Chicago Department of Economics",
                "UChicago Economics",
            ),
        ),
        roster_url="https://economics.uchicago.edu/people/faculty",
        fetch_entries=_fetch_uchicago,
    ),
    "princeton": SchoolRoster(
        key="princeton",
        org=OrgSpec(
            university=("Princeton University", "Princeton"),
            school=("Princeton Faculty of Arts and Sciences", "Princeton FAS"),
            department=(
                "Princeton Department of Economics",
                "Princeton Economics",
            ),
        ),
        roster_url="https://economics.princeton.edu/people/",
        fetch_entries=_fetch_princeton,
    ),
    "harvard": SchoolRoster(
        key="harvard",
        org=OrgSpec(
            university=("Harvard University", "Harvard"),
            school=("Harvard Faculty of Arts and Sciences", "Harvard FAS"),
            department=("Harvard Department of Economics", "Harvard Economics"),
        ),
        roster_url="https://www.economics.harvard.edu/faculty",
        fetch_entries=lambda _fetcher: [],
    ),
}


def _find_org_by_name(session: Session, name: str) -> int | None:
    row = session.execute(
        text("SELECT id FROM organizations WHERE name = :name LIMIT 1"),
        {"name": name},
    ).scalar()
    return int(row) if row is not None else None


def _ensure_org(
    session: Session,
    *,
    name: str,
    short_name: str | None,
    kind: str,
    country: str = "US",
) -> int:
    existing = _find_org_by_name(session, name)
    if existing is not None:
        return existing
    org_id = int(
        session.execute(
            text(
                """
                INSERT INTO organizations (name, short_name, country, kind)
                VALUES (:name, :short, :country, :kind)
                RETURNING id
                """
            ),
            {
                "name": name,
                "short": short_name,
                "country": country,
                "kind": kind,
            },
        ).scalar_one()
    )
    return org_id


def _ensure_org_relationship(
    session: Session, *, child_org_id: int, parent_org_id: int
) -> None:
    exists = session.execute(
        text(
            """
            SELECT 1 FROM org_relationships
            WHERE child_org_id = :child
              AND parent_org_id = :parent
              AND relationship_type = 'primary'
              AND ends_at IS NULL
            """
        ),
        {"child": child_org_id, "parent": parent_org_id},
    ).scalar()
    if exists:
        return
    session.execute(
        text(
            """
            INSERT INTO org_relationships
              (child_org_id, parent_org_id, relationship_type, verification_status)
            VALUES (:child, :parent, 'primary', 'verified')
            """
        ),
        {"child": child_org_id, "parent": parent_org_id},
    )


def ensure_school_tree(session: Session, org: OrgSpec) -> int:
    """Return the department org id, creating university/school/dept as needed."""
    university_id = _ensure_org(
        session,
        name=org.university[0],
        short_name=org.university[1],
        kind="university",
    )
    parent_id = university_id
    if org.school is not None:
        school_id = _ensure_org(
            session,
            name=org.school[0],
            short_name=org.school[1],
            kind="school",
        )
        _ensure_org_relationship(
            session, child_org_id=school_id, parent_org_id=university_id
        )
        parent_id = school_id
    department_id = _ensure_org(
        session,
        name=org.department[0],
        short_name=org.department[1],
        kind="department",
    )
    _ensure_org_relationship(
        session, child_org_id=department_id, parent_org_id=parent_id
    )
    return department_id


def _load_person_indexes(
    session: Session,
) -> tuple[dict[str, int], dict[str, int]]:
    url_map: dict[str, int] = {}
    rows = session.execute(
        text(
            """
            SELECT external_id, person_id
            FROM external_identifiers
            WHERE provider = 'official_url' AND person_id IS NOT NULL
            """
        )
    ).all()
    for url, person_id in rows:
        url_map[str(url)] = int(person_id)

    name_hits: dict[str, list[int]] = {}
    people = session.execute(
        text("SELECT id, firstname, lastname FROM people")
    ).all()
    for person_id, first, last in people:
        key = _normalize_name_key(first, last)
        name_hits.setdefault(key, []).append(int(person_id))
    name_map = {k: v[0] for k, v in name_hits.items() if len(v) == 1}
    return url_map, name_map


def _find_person(
    entry: RosterEntry,
    *,
    url_map: dict[str, int],
    name_map: dict[str, int],
) -> int | None:
    if entry.profile_url and entry.profile_url in url_map:
        return url_map[entry.profile_url]
    first, _middle, last = _split_name(entry.display_name)
    return name_map.get(_normalize_name_key(first, last))


def _create_person(session: Session, display_name: str) -> int:
    first, middle, last = _split_name(display_name)
    return int(
        session.execute(
            text(
                """
                INSERT INTO people (firstname, middlename, lastname)
                VALUES (:f, :m, :l)
                RETURNING id
                """
            ),
            {"f": first, "m": middle, "l": last},
        ).scalar_one()
    )


def _current_primary_affiliation(session: Session, person_id: int) -> int | None:
    row = session.execute(
        text(
            """
            SELECT id FROM person_affiliations
            WHERE person_id = :p AND is_primary AND ends_at IS NULL
            ORDER BY starts_at DESC NULLS LAST, id DESC
            LIMIT 1
            """
        ),
        {"p": person_id},
    ).scalar()
    return int(row) if row is not None else None


def _affiliation_has_anchor(
    session: Session, affiliation_id: int, organization_id: int
) -> bool:
    row = session.execute(
        text(
            """
            SELECT 1 FROM affiliation_org_assignments
            WHERE affiliation_id = :a
              AND organization_id = :o
              AND assignment_type = 'chart_anchor'
            """
        ),
        {"a": affiliation_id, "o": organization_id},
    ).scalar()
    return row is not None


def ensure_roster_membership(
    session: Session,
    *,
    person_id: int,
    department_id: int,
    title: str | None,
    snapshot_id: int | None,
) -> tuple[str, int | None]:
    """Attach a chart-anchored primary affiliation when missing."""
    rank = classify_position_rank(title)
    aff_title = (title or "Faculty").strip()
    affiliation_kind = infer_affiliation_kind(aff_title, section="employment")

    aff_id = _current_primary_affiliation(session, person_id)
    if aff_id is None:
        aff_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO person_affiliations
                      (person_id, title, affiliation_kind, position_rank,
                       is_primary, verification_status)
                    VALUES (:p, :t, :k, :r, TRUE, 'verified')
                    RETURNING id
                    """
                ),
                {
                    "p": person_id,
                    "t": aff_title,
                    "k": affiliation_kind,
                    "r": rank,
                },
            ).scalar_one()
        )
        status = "affiliation_created"
    elif _affiliation_has_anchor(session, aff_id, department_id):
        session.execute(
            text(
                """
                UPDATE person_affiliations
                SET title = COALESCE(title, :t),
                    position_rank = COALESCE(position_rank, :r)
                WHERE id = :a
                """
            ),
            {"t": aff_title, "r": rank, "a": aff_id},
        )
        status = "affiliation_updated"
    else:
        aff_id = int(
            session.execute(
                text(
                    """
                    INSERT INTO person_affiliations
                      (person_id, title, affiliation_kind, position_rank,
                       is_primary, verification_status)
                    VALUES (:p, :t, :k, :r, FALSE, 'verified')
                    RETURNING id
                    """
                ),
                {
                    "p": person_id,
                    "t": aff_title,
                    "k": affiliation_kind,
                    "r": rank,
                },
            ).scalar_one()
        )
        status = "secondary_affiliation_created"

    session.execute(
        text(
            """
            INSERT INTO affiliation_org_assignments
              (affiliation_id, organization_id, assignment_type)
            VALUES (:a, :o, 'chart_anchor')
            ON CONFLICT DO NOTHING
            """
        ),
        {"a": aff_id, "o": department_id},
    )

    if snapshot_id is not None:
        session.execute(
            text(
                """
                INSERT INTO evidence (snapshot_id, label, affiliation_id)
                SELECT :s, 'roster:affiliation', :a
                WHERE NOT EXISTS (
                  SELECT 1 FROM evidence
                  WHERE snapshot_id = :s
                    AND affiliation_id = :a
                    AND label = 'roster:affiliation'
                )
                """
            ),
            {"s": snapshot_id, "a": aff_id},
        )
    return status, aff_id


def ingest_school(
    session: Session,
    school: SchoolRoster,
    *,
    fetcher: Fetcher,
    dry_run: bool,
) -> Counter:
    stats: Counter = Counter()
    department_id = ensure_school_tree(session, school.org)
    stats["orgs_ensured"] += 1

    if school.key == "harvard":
        stats["harvard_skipped_blocked"] += 1
        return stats

    status, body, _ = fetcher.fetch(school.roster_url)
    if status >= 400:
        raise RuntimeError(f"{school.roster_url} returned HTTP {status}")
    snapshot_id = write_snapshot(
        session,
        url=school.roster_url,
        source_kind="official_roster",
        body=body,
        http_status=status,
    )

    entries = school.fetch_entries(fetcher)
    stats["roster_entries"] += len(entries)
    url_map, name_map = _load_person_indexes(session)

    for entry in entries:
        person_id = _find_person(entry, url_map=url_map, name_map=name_map)
        if person_id is None:
            person_id = _create_person(session, entry.display_name)
            stats["people_created"] += 1
            first, _middle, last = _split_name(entry.display_name)
            name_map[_normalize_name_key(first, last)] = person_id
        else:
            stats["people_matched"] += 1

        if entry.profile_url:
            if upsert_external_identifier(
                session,
                provider="official_url",
                external_id=entry.profile_url,
                person_id=person_id,
                snapshot_id=snapshot_id,
            ):
                stats["official_urls_linked"] += 1
                url_map[entry.profile_url] = person_id

        aff_status, _aff_id = ensure_roster_membership(
            session,
            person_id=person_id,
            department_id=department_id,
            title=entry.title,
            snapshot_id=snapshot_id,
        )
        stats[aff_status] += 1

    return stats


def refresh_roster_views(session: Session) -> None:
    session.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY org_tree_current"))
    session.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY person_anchor"))
    session.execute(text("REFRESH MATERIALIZED VIEW CONCURRENTLY org_current_roster"))


def run(
    *,
    schools: list[str],
    dry_run: bool = False,
    fill_profiles: bool = False,
) -> None:
    fetcher = Fetcher()
    totals: Counter = Counter()

    with _SessionLocal() as session:
        for key in schools:
            school = SCHOOLS.get(key)
            if school is None:
                raise SystemExit(f"Unknown school {key!r}; choose from {sorted(SCHOOLS)}")
            print(f"Ingesting {key} roster from {school.roster_url} …", flush=True)
            per = ingest_school(session, school, fetcher=fetcher, dry_run=dry_run)
            totals.update(per)
            for stat_key, value in sorted(per.items()):
                print(f"  {stat_key}: {value}")

            if dry_run:
                session.rollback()
            else:
                session.commit()

        if not dry_run:
            print("Refreshing org/person materialized views …", flush=True)
            refresh_roster_views(session)
            session.commit()

    print("\n=== roster ingest summary ===")
    for key in sorted(totals):
        print(f"  {key:.<28s} {totals[key]}")

    if fill_profiles and not dry_run:
        from scripts.backfill import profiles as profile_backfill

        print("\nFilling profile pages …", flush=True)
        profile_backfill.run(
            parse_only=False,
            dry_run=False,
            overwrite=False,
            limit=None,
            only_host=None,
            verbose=False,
        )


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--schools",
        default="mit,yale,uchicago,princeton,harvard",
        help="Comma-separated school keys (default: all five).",
    )
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--fill-profiles",
        action="store_true",
        help="Run scripts.backfill.profiles after roster ingest.",
    )
    args = parser.parse_args(argv)
    schools = [part.strip().lower() for part in args.schools.split(",") if part.strip()]
    run(schools=schools, dry_run=args.dry_run, fill_profiles=args.fill_profiles)


if __name__ == "__main__":
    main()
