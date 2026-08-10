"""Shared plumbing for `scripts.backfill.*`.

Design principles
-----------------
- Snapshots are the single source of truth for what we fetched. Every extracted
  fact links back to a snapshot row via `evidence`. That's why we hash the raw
  response body and keep it on disk under `data/ingest/raw/`.
- URLs already live in `external_identifiers` (provider = 'official_url').
  Backfillers never invent URLs; they only iterate over what's already anchored
  to a `person_id` or `organization_id`.
- Parsers are pure: they take (url, html) and return a `ProfileExtraction`.
  Writing to the DB happens here, so we can dedup and add provenance in one
  place regardless of the source school.
- Politeness is enforced per-host. Nobody hammers a single origin.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import urllib.request
import urllib.error

from sqlalchemy import text
from sqlalchemy.orm import Session

from db.models.enums import POSITION_RANK_VALUES

# backend/scripts/backfill/common.py -> parents[3] is the repo root.  The
# physical files land there even when the scripts run from `backend/`; the
# *stored* path stays relative ("data/ingest/raw/…") so api/main.py can
# resolve it against the repo root (see write_snapshot).
REPO_ROOT = Path(__file__).resolve().parents[3]
RAW_DIR = REPO_ROOT / "data" / "ingest" / "raw"
RAW_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "elmonte-backfill/0.1 (research directory; contact: chendyu@berkeley.edu)"
DEFAULT_TIMEOUT = 20.0
MIN_HOST_INTERVAL = 1.1  # seconds between requests to the same host
MAX_CV_TITLE_LEN = 180


# --- Data shapes ------------------------------------------------------------


@dataclass
class PublicationCandidate:
    """One publication as extracted from a profile page.

    Not persisted directly — first-pass Stanford Econ ingest defers publication
    writes because department pages rarely have DOIs or clean author lists.
    This dataclass exists so parsers can still surface what they see for later
    OpenAlex reconciliation.
    """

    title: str
    year: int | None = None
    venue: str | None = None
    doi: str | None = None
    url: str | None = None


@dataclass
class ProfileExtraction:
    """Structured shape every parser returns.

    Optional-heavy on purpose: department pages vary. Downstream code checks
    each field individually before writing.
    """

    title: str | None = None
    biography: str | None = None
    orcid: str | None = None
    personal_url: str | None = None
    publications: list[PublicationCandidate] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


@dataclass
class CvAffiliationCandidate:
    """One appointment or degree parsed from a CV."""

    title: str
    organization: str
    affiliation_kind: str
    position_rank: str | None = None
    starts_at: datetime | None = None
    ends_at: datetime | None = None


# --- Fetcher ----------------------------------------------------------------


class PoliteFetcher:
    """Simple synchronous HTTP fetcher with per-host throttling.

    Enough for a few hundred URLs across four hosts. If we ever need
    concurrency we'll swap in httpx.AsyncClient, but for backfill runs that
    happen once every few weeks this is fine and easy to reason about.
    """

    def __init__(
        self,
        user_agent: str = USER_AGENT,
        min_interval: float = MIN_HOST_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_hit: dict[str, float] = {}

    def _wait(self, host: str) -> None:
        last = self._last_hit.get(host)
        if last is None:
            return
        gap = time.monotonic() - last
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)

    def fetch(self, url: str, *, accept: str | None = None) -> tuple[int, bytes, dict[str, str]]:
        """Fetch `url` and return (status, body, headers).

        Raises on transport errors so the orchestrator can decide whether to
        retry, skip, or abort. Status codes >= 400 are returned as-is so we
        can still record the snapshot with the failure status.
        """
        host = urlparse(url).netloc
        self._wait(host)
        headers = {
            "User-Agent": self.user_agent,
            "Accept-Language": "en-US,en;q=0.8",
        }
        if accept:
            headers["Accept"] = accept
        else:
            headers["Accept"] = "text/html,application/xhtml+xml"
        req = urllib.request.Request(url, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                body = resp.read()
                status = int(resp.status)
                headers = {k.lower(): v for k, v in resp.headers.items()}
        except urllib.error.HTTPError as err:
            body = err.read() or b""
            status = int(err.code)
            headers = {k.lower(): v for k, v in err.headers.items()} if err.headers else {}
        finally:
            self._last_hit[host] = time.monotonic()
        return status, body, headers


# --- Snapshot expiry --------------------------------------------------------

SOURCE_KIND_TTL_DAYS: dict[str, int] = {
    "official_roster": 365,
    "official_profile": 365,
    "openalex": 365,
    "crossref": 365,
    "ror": 365,
    "news": 365,
    "manual": 365,
}


def snapshot_expiry(source_kind: str) -> int | None:
    """Return the TTL in days for a source_kind, or None if no explicit expiry."""
    return SOURCE_KIND_TTL_DAYS.get(source_kind)


# --- Snapshot writer --------------------------------------------------------


def write_snapshot(
    session: Session,
    *,
    url: str,
    source_kind: str,
    body: bytes,
    http_status: int | None = None,
    file_ext: str = ".html",
    ttl_days: int | None = None,
) -> int:
    """Upsert a `source_snapshots` row, dedup by (source_url, content_hash).

    Writes the body to `data/ingest/raw/{hash}{file_ext}` if not already present.
    Returns the snapshot id.
    """
    content_hash = hashlib.sha256(body).hexdigest()
    raw_file = RAW_DIR / f"{content_hash}{file_ext}"
    if not raw_file.exists():
        raw_file.write_bytes(body)
    # Stored path stays repo-root-relative — api/main.py resolves it against
    # the repo root when serving, and the DB remains portable across machines.
    local_path = Path("data/ingest/raw") / f"{content_hash}{file_ext}"

    existing = session.execute(
        text(
            "SELECT id FROM source_snapshots "
            "WHERE source_url = :u AND content_hash = :h"
        ),
        {"u": url, "h": content_hash},
    ).scalar()
    if existing is not None:
        return int(existing)

    # Compute expires_at: explicit ttl_days takes precedence over source_kind mapping.
    days = ttl_days if ttl_days is not None else snapshot_expiry(source_kind)
    expires_at = None
    if days is not None:
        expires_at = datetime.now(timezone.utc) + timedelta(days=days)

    row = session.execute(
        text(
            """
            INSERT INTO source_snapshots
                (source_url, source_kind, content_hash, local_path, http_status, expires_at)
            VALUES (:u, :k, :h, :p, :s, :e)
            RETURNING id
            """
        ),
        {
            "u": url,
            "k": source_kind,
            "h": content_hash,
            "p": str(local_path),
            "s": http_status,
            "e": expires_at,
        },
    ).scalar_one()
    return int(row)


def load_snapshot_body(session: Session, snapshot_id: int) -> tuple[str, str] | None:
    """Return (url, html) for a snapshot if the local HTML file still exists."""
    row = session.execute(
        text(
            "SELECT source_url, local_path FROM source_snapshots WHERE id = :i"
        ),
        {"i": snapshot_id},
    ).mappings().first()
    if row is None or not row["local_path"]:
        return None
    path = Path(row["local_path"])
    if not path.exists():
        return None
    return row["source_url"], path.read_text(errors="replace")


def load_snapshot_bytes(session: Session, snapshot_id: int) -> tuple[str, bytes] | None:
    """Return (url, body) for a snapshot if the local file still exists."""
    row = session.execute(
        text("SELECT source_url, local_path FROM source_snapshots WHERE id = :i"),
        {"i": snapshot_id},
    ).mappings().first()
    if row is None or not row["local_path"]:
        return None
    path = Path(row["local_path"])
    if not path.exists():
        return None
    return row["source_url"], path.read_bytes()


def resolve_organization_id(session: Session, label: str) -> int | None:
    """Best-effort match of a free-text org label to `organizations.id`."""
    cleaned = normalize_whitespace(label)
    if not cleaned:
        return None

    queries = [cleaned]
    if "," in cleaned:
        queries.append(cleaned.split(",")[-1].strip())
        queries.append(cleaned.split(",")[0].strip())

    seen: set[str] = set()
    for query in queries:
        key = query.lower()
        if not key or key in seen:
            continue
        seen.add(key)
        row = session.execute(
            text(
                """
                SELECT id
                FROM organizations
                WHERE name ILIKE :q OR short_name ILIKE :q
                ORDER BY
                  CASE WHEN name ILIKE :q THEN 0 ELSE 1 END,
                  length(name)
                LIMIT 1
                """
            ),
            {"q": query},
        ).scalar()
        if row is not None:
            return int(row)

    row = session.execute(
        text(
            """
            SELECT id
            FROM organizations
            WHERE :q ILIKE '%' || name || '%'
               OR name ILIKE '%' || :q || '%'
            ORDER BY length(name) DESC
            LIMIT 1
            """
        ),
        {"q": cleaned},
    ).scalar()
    return int(row) if row is not None else None


def affiliation_exists(
    session: Session,
    *,
    person_id: int,
    title: str,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> bool:
    row = session.execute(
        text(
            """
            SELECT 1
            FROM person_affiliations
            WHERE person_id = :p
              AND title = :t
              AND starts_at IS NOT DISTINCT FROM :s
              AND ends_at IS NOT DISTINCT FROM :e
            LIMIT 1
            """
        ),
        {"p": person_id, "t": title, "s": starts_at, "e": ends_at},
    ).scalar()
    return row is not None


def insert_timeline_affiliation(
    session: Session,
    *,
    person_id: int,
    candidate: CvAffiliationCandidate,
    snapshot_id: int,
    evidence_label: str,
) -> tuple[str, int | None]:
    """Insert one parsed affiliation when it is not already present."""
    title = candidate.title.strip()
    if not title:
        return "skipped", None
    if len(title) > MAX_CV_TITLE_LEN:
        return "skipped", None

    position_rank = candidate.position_rank
    if position_rank is not None and position_rank not in POSITION_RANK_VALUES:
        position_rank = None

    if affiliation_exists(
        session,
        person_id=person_id,
        title=title,
        starts_at=candidate.starts_at,
        ends_at=candidate.ends_at,
    ):
        return "duplicate", None

    aff_id = int(
        session.execute(
            text(
                """
                INSERT INTO person_affiliations
                  (person_id, title, affiliation_kind, position_rank,
                   is_primary, starts_at, ends_at, verification_status)
                VALUES
                  (:p, :t, :k, :r, FALSE, :s, :e, 'unverified')
                RETURNING id
                """
            ),
            {
                "p": person_id,
                "t": title,
                "k": candidate.affiliation_kind,
                "r": position_rank,
                "s": candidate.starts_at,
                "e": candidate.ends_at,
            },
        ).scalar_one()
    )

    org_id = resolve_organization_id(session, candidate.organization)
    if org_id is not None:
        session.execute(
            text(
                """
                INSERT INTO affiliation_org_assignments
                  (affiliation_id, organization_id, assignment_type)
                VALUES (:a, :o, 'chart_anchor')
                ON CONFLICT DO NOTHING
                """
            ),
            {"a": aff_id, "o": org_id},
        )

    add_evidence(
        session,
        snapshot_id=snapshot_id,
        label=evidence_label,
        affiliation_id=aff_id,
    )
    return "written", aff_id


def insert_cv_affiliation(
    session: Session,
    *,
    person_id: int,
    candidate: CvAffiliationCandidate,
    snapshot_id: int,
) -> tuple[str, int | None]:
    return insert_timeline_affiliation(
        session,
        person_id=person_id,
        candidate=candidate,
        snapshot_id=snapshot_id,
        evidence_label="cv:affiliation",
    )


def upsert_person_cv(
    session: Session,
    *,
    person_id: int,
    cv_url: str,
    cv_snapshot_id: int,
    overwrite: bool = False,
) -> bool:
    """Point a person at a cached CV snapshot for in-app viewing."""
    if overwrite:
        result = session.execute(
            text(
                """
                UPDATE people
                SET cv_url = :u, cv_snapshot_id = :s
                WHERE id = :i
                """
            ),
            {"u": cv_url, "s": cv_snapshot_id, "i": person_id},
        )
    else:
        result = session.execute(
            text(
                """
                UPDATE people
                SET cv_url = :u, cv_snapshot_id = :s
                WHERE id = :i
                  AND (cv_snapshot_id IS NULL OR cv_snapshot_id = :s)
                """
            ),
            {"u": cv_url, "s": cv_snapshot_id, "i": person_id},
        )
    if not result.rowcount:
        return False

    session.execute(
        text(
            """
            INSERT INTO evidence (snapshot_id, label, person_id)
            SELECT :s, 'cv:document', :p
            WHERE NOT EXISTS (
              SELECT 1 FROM evidence
              WHERE snapshot_id = :s
                AND person_id = :p
                AND label = 'cv:document'
            )
            """
        ),
        {"s": cv_snapshot_id, "p": person_id},
    )
    return True


def clear_timeline_affiliations(
    session: Session, person_id: int, *, evidence_label: str
) -> int:
    """Delete affiliations imported under a specific evidence label."""
    aff_ids = [
        int(row)
        for row in session.execute(
            text(
                """
                SELECT pa.id
                FROM person_affiliations pa
                JOIN evidence e ON e.affiliation_id = pa.id
                WHERE pa.person_id = :p
                  AND e.label = :l
                """
            ),
            {"p": person_id, "l": evidence_label},
        ).scalars()
    ]
    for aff_id in aff_ids:
        session.execute(
            text("DELETE FROM evidence WHERE affiliation_id = :a"), {"a": aff_id}
        )
        session.execute(
            text("DELETE FROM affiliation_org_assignments WHERE affiliation_id = :a"),
            {"a": aff_id},
        )
        session.execute(
            text("DELETE FROM person_affiliations WHERE id = :a"), {"a": aff_id}
        )
    return len(aff_ids)


def clear_cv_affiliations(session: Session, person_id: int) -> int:
    return clear_timeline_affiliations(
        session, person_id, evidence_label="cv:affiliation"
    )


def utc_year_start(year: int) -> datetime:
    return datetime(year, 1, 1, tzinfo=timezone.utc)


def utc_year_end(year: int) -> datetime:
    return datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)


# --- Fact writers -----------------------------------------------------------


def add_evidence(
    session: Session,
    *,
    snapshot_id: int,
    label: str | None = None,
    person_id: int | None = None,
    affiliation_id: int | None = None,
) -> None:
    """Insert a minimal evidence row. Extend the kwargs as we support more
    subjects. The `evidence_exactly_one_subject` CHECK constraint enforces
    that exactly one of the *_id columns is non-null; the caller must comply.
    """
    session.execute(
        text(
            """
            INSERT INTO evidence
              (snapshot_id, label, person_id, affiliation_id)
            VALUES (:s, :l, :p, :a)
            """
        ),
        {
            "s": snapshot_id,
            "l": label,
            "p": person_id,
            "a": affiliation_id,
        },
    )


def upsert_homepage_url(
    session: Session, *, person_id: int, homepage_url: str, overwrite: bool = False
) -> bool:
    """Write the person's personal-website URL to `people.homepage_url`.

    Only fills NULL by default. The CHECK constraint enforces http/https, so
    invalid URLs will raise; the parser is responsible for filtering junk
    before we get here.
    """
    if overwrite:
        result = session.execute(
            text("UPDATE people SET homepage_url = :u WHERE id = :i"),
            {"u": homepage_url, "i": person_id},
        )
    else:
        result = session.execute(
            text(
                "UPDATE people SET homepage_url = :u "
                "WHERE id = :i AND homepage_url IS NULL"
            ),
            {"u": homepage_url, "i": person_id},
        )
    return bool(result.rowcount)


def upsert_biography(
    session: Session, *, person_id: int, biography: str, overwrite: bool = False
) -> bool:
    """Write biography to `people.biography`.

    By default only fills when currently NULL, so multiple ingest runs don't
    thrash existing content. Returns True if a write happened.
    """
    if overwrite:
        result = session.execute(
            text("UPDATE people SET biography = :b WHERE id = :i"),
            {"b": biography, "i": person_id},
        )
    else:
        result = session.execute(
            text(
                "UPDATE people SET biography = :b "
                "WHERE id = :i AND biography IS NULL"
            ),
            {"b": biography, "i": person_id},
        )
    return bool(result.rowcount)


def upsert_current_affiliation_title(
    session: Session,
    *,
    person_id: int,
    title: str,
    overwrite: bool = False,
) -> tuple[int, bool] | None:
    """Set `person_affiliations.title` on the person's current primary
    affiliation.

    "Current primary" = is_primary AND ends_at IS NULL. This mirrors what the
    graph resolvers use as the anchor row.

    Returns:
        None                 — person has no current primary affiliation
        (aff_id, True)       — we wrote a new title
        (aff_id, False)      — row exists but we didn't write (already set,
                               and overwrite=False)
    """
    row = session.execute(
        text(
            """
            SELECT id, title FROM person_affiliations
            WHERE person_id = :p AND is_primary AND ends_at IS NULL
            ORDER BY starts_at DESC NULLS LAST, id DESC
            LIMIT 1
            """
        ),
        {"p": person_id},
    ).mappings().first()
    if row is None:
        return None
    aff_id = int(row["id"])
    if row["title"] and not overwrite:
        return aff_id, False
    session.execute(
        text("UPDATE person_affiliations SET title = :t WHERE id = :i"),
        {"t": title, "i": aff_id},
    )
    return aff_id, True


def upsert_external_identifier(
    session: Session,
    *,
    provider: str,
    external_id: str,
    person_id: int | None = None,
    organization_id: int | None = None,
    snapshot_id: int | None = None,
) -> bool:
    """Insert an external identifier row. Skips on any uniqueness conflict.

    The table has two overlapping unique indexes we care about:
      - (provider, external_id) globally unique
      - one (provider, person_id) or (provider, organization_id) per subject

    Returns True if we inserted, False if it was a no-op.
    """
    result = session.execute(
        text(
            """
            INSERT INTO external_identifiers
              (provider, external_id, person_id, organization_id, snapshot_id)
            VALUES (:pr, :eid, :pid, :oid, :sid)
            ON CONFLICT DO NOTHING
            """
        ),
        {
            "pr": provider,
            "eid": external_id,
            "pid": person_id,
            "oid": organization_id,
            "sid": snapshot_id,
        },
    )
    return bool(result.rowcount)


# --- Parser registry --------------------------------------------------------


ParserFn = Callable[[str, str], ProfileExtraction]

# Populated lazily to avoid import cycles; the profile orchestrator wires
# concrete parsers in via `register_parser`.
_REGISTRY: dict[str, ParserFn] = {}


def register_parser(host: str, fn: ParserFn) -> None:
    _REGISTRY[host] = fn


def parser_for(url: str) -> ParserFn | None:
    return _REGISTRY.get(urlparse(url).netloc)


def known_hosts() -> Iterable[str]:
    return _REGISTRY.keys()


# --- Text helpers used by multiple parsers ---------------------------------


def normalize_whitespace(s: str) -> str:
    """Collapse runs of whitespace, trim, keep single line breaks."""
    lines = [ln.strip() for ln in s.splitlines()]
    lines = [ln for ln in lines if ln]
    return "\n".join(lines)


def strip_html(s: str) -> str:
    """Very light tag stripper for meta/description text.

    Not a full HTML parser — good enough for `<meta>` content attribute values
    and short bio fragments that don't nest. Also decodes HTML entities so
    `&nbsp;`, `&amp;`, etc. don't leak into stored bios.
    """
    import re
    from html import unescape

    text_only = re.sub(r"<[^>]+>", " ", s)
    text_only = unescape(text_only)
    text_only = text_only.replace("\u00a0", " ")  # non-breaking space
    return normalize_whitespace(text_only).strip()


def clean_publication_title(raw: str) -> str:
    """Normalize publication titles from OpenAlex and other ingest sources.

    OpenAlex sometimes concatenates acknowledgment footnotes onto titles,
    prefixed with ✶ and HTML entities like ``&amp;``.
    """
    from html import unescape

    title = unescape(raw).replace("\u00a0", " ")
    title = " ".join(title.split())

    if "✶" in title:
        title = title.split("✶", 1)[0].strip()

    for marker in (
        " The authors thank ",
        " Acknowledgments ",
        " Acknowledgements ",
        " We thank ",
        " I thank ",
    ):
        idx = title.find(marker)
        if idx > 15:
            title = title[:idx].strip()
            break

    return title.strip()


_JUNK_TITLE_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\.(do|tab|dta|r|sql|sas|m|py)$", re.IGNORECASE),
    re.compile(r"\.(pptx?|pdf|docx?|key|odp)$", re.IGNORECASE),
    re.compile(r"^(poster|slides|handout|abstract)[ _]", re.IGNORECASE),
    re.compile(r"^readme(\.txt)?$", re.IGNORECASE),
    re.compile(r"^header\.do$", re.IGNORECASE),
    re.compile(r"^(app|appx)_(paper|fig|tab)", re.IGNORECASE),
    re.compile(r"^(paper|appx)_(fig|tab)_", re.IGNORECASE),
    re.compile(r"^mrc_", re.IGNORECASE),
    re.compile(r"^supplemental\b", re.IGNORECASE),
    re.compile(r"^online appendix\b", re.IGNORECASE),
    # Edited-volume / handbook chapters mis-ingested as papers.
    re.compile(r"^\d{1,2}\.\s+\S"),
)

_BOOK_CHAPTER_DOI_PATTERN = re.compile(r"978\d{10}-\d{3,4}$")


def is_book_chapter_doi(doi: str | None) -> bool:
    """True for DOIs that point at a book chapter fragment, not a journal article."""
    if not doi:
        return False
    doi_id = doi.removeprefix("https://doi.org/").strip()
    return bool(_BOOK_CHAPTER_DOI_PATTERN.search(doi_id))


def is_junk_publication_title(raw: str) -> bool:
    """True for code artifacts, README stubs, and other non-paper OpenAlex works."""
    title = clean_publication_title(raw).strip()
    if len(title) < 4:
        return True
    return any(pattern.search(title) for pattern in _JUNK_TITLE_PATTERNS)


DUPLICATE_PUBLICATION_YEAR_TOLERANCE = 2


def publications_likely_same_paper(
    title_a: str,
    year_a: int | None,
    title_b: str,
    year_b: int | None,
) -> bool:
    """Whether two bibliography rows likely describe one paper."""
    if not publication_titles_equivalent(title_a, title_b):
        return False
    if year_a is None or year_b is None:
        return True
    return abs(int(year_a) - int(year_b)) <= DUPLICATE_PUBLICATION_YEAR_TOLERANCE


def normalize_title_for_dedupe(raw: str) -> str:
    """Aggressive title normalization for duplicate detection."""
    title = clean_publication_title(raw).lower()
    title = re.sub(r"^replication (data|materials|package) for:\s*", "", title)
    title = re.sub(r"\*+$", "", title).strip()
    title = re.sub(r"\.\s*nber working paper.*$", "", title, flags=re.IGNORECASE)
    title = re.sub(r"\s+#\d+$", "", title)
    title = re.sub(r"[^\w\s]", " ", title)
    return " ".join(title.split())


def publication_titles_equivalent(a: str, b: str) -> bool:
    """Whether two titles likely refer to the same paper."""
    left = normalize_title_for_dedupe(a)
    right = normalize_title_for_dedupe(b)
    if not left or not right:
        return False
    if left == right:
        return True
    short, long = (left, right) if len(left) <= len(right) else (right, left)
    return len(short) >= 30 and long.startswith(short)


def is_displayable_publication(raw: str) -> bool:
    """Whether a bibliography row should appear in the UI."""
    if is_junk_publication_title(raw):
        return False
    title = clean_publication_title(raw).lower()
    if title.startswith("replication data for:"):
        return False
    return True


def should_skip_openalex_work(work: dict[str, Any]) -> bool:
    """Drop OpenAlex works that are not real research outputs."""
    title = (work.get("title") or work.get("display_name") or "").strip()
    if is_junk_publication_title(title):
        return True
    if is_book_chapter_doi((work.get("doi") or "").strip() or None):
        return True
    work_type = (work.get("type") or "").lower()
    if work_type in {
        "book-chapter",
        "reference-entry",
        "paratext",
        "letter",
        "editorial",
        "erratum",
        "correction",
    }:
        return True
    if work_type == "dataset" and re.search(
        r"^replication (data|materials)", title, re.IGNORECASE
    ):
        # Keep dataset rows that will dedupe onto the parent paper title.
        return False
    if work_type == "dataset" and is_junk_publication_title(title):
        return True
    return False


def resolve_berkeley_university_org_id(session: Session) -> int:
    """Return organizations.id for UC Berkeley (university row)."""
    row = session.execute(
        text(
            """
            SELECT id
            FROM organizations
            WHERE kind = 'university'
              AND name ILIKE :pat
            ORDER BY id
            LIMIT 1
            """
        ),
        {"pat": "%California, Berkeley%"},
    ).scalar()
    if row is None:
        raise RuntimeError(
            "UC Berkeley university organization not found in organizations table"
        )
    return int(row)


def sql_person_is_berkeley_anchored(person_id_sql: str) -> str:
    """SQL ``EXISTS`` subquery for a Berkeley primary anchor.

    Bind ``:berkeley_org_id`` in the outer query. Prefix with ``AND`` when used
    after other ``WHERE`` conditions.
    """
    return f"""
        EXISTS (
          SELECT 1
          FROM person_anchor pa
          JOIN org_tree_current t ON t.organization_id = pa.organization_id
          WHERE pa.person_id = {person_id_sql}
            AND pa.validity @> CURRENT_DATE
            AND pa.is_primary
            AND :berkeley_org_id = ANY(t.ancestor_ids)
        )
    """


def canonicalize_orcid(raw: str) -> str | None:
    """Return a bare `0000-0000-0000-0000` id, or None if it doesn't look
    like a valid ORCID. Accepts URLs and various punctuation."""
    import re

    m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", raw)
    return m.group(1) if m else None


# Force `Any` import to keep type-only usage explicit and avoid linter noise
_ = Any
