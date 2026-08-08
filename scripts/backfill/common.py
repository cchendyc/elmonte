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
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable
from urllib.parse import urlparse

import urllib.request
import urllib.error

from sqlalchemy import text
from sqlalchemy.orm import Session

RAW_DIR = Path("data/ingest/raw")
RAW_DIR.mkdir(parents=True, exist_ok=True)

USER_AGENT = "elmonte-backfill/0.1 (research directory; contact: chendyu@berkeley.edu)"
DEFAULT_TIMEOUT = 20.0
MIN_HOST_INTERVAL = 1.1  # seconds between requests to the same host


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

    def fetch(self, url: str) -> tuple[int, bytes, dict[str, str]]:
        """Fetch `url` and return (status, body, headers).

        Raises on transport errors so the orchestrator can decide whether to
        retry, skip, or abort. Status codes >= 400 are returned as-is so we
        can still record the snapshot with the failure status.
        """
        host = urlparse(url).netloc
        self._wait(host)
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.8",
            },
        )
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


# --- Snapshot writer --------------------------------------------------------


def write_snapshot(
    session: Session,
    *,
    url: str,
    source_kind: str,
    body: bytes,
    http_status: int | None = None,
) -> int:
    """Upsert a `source_snapshots` row, dedup by (source_url, content_hash).

    Writes the body to `data/ingest/raw/{hash}.html` if not already present.
    Returns the snapshot id.
    """
    content_hash = hashlib.sha256(body).hexdigest()
    local_path = RAW_DIR / f"{content_hash}.html"
    if not local_path.exists():
        local_path.write_bytes(body)

    existing = session.execute(
        text(
            "SELECT id FROM source_snapshots "
            "WHERE source_url = :u AND content_hash = :h"
        ),
        {"u": url, "h": content_hash},
    ).scalar()
    if existing is not None:
        return int(existing)

    row = session.execute(
        text(
            """
            INSERT INTO source_snapshots
                (source_url, source_kind, content_hash, local_path, http_status)
            VALUES (:u, :k, :h, :p, :s)
            RETURNING id
            """
        ),
        {
            "u": url,
            "k": source_kind,
            "h": content_hash,
            "p": str(local_path),
            "s": http_status,
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


def canonicalize_orcid(raw: str) -> str | None:
    """Return a bare `0000-0000-0000-0000` id, or None if it doesn't look
    like a valid ORCID. Accepts URLs and various punctuation."""
    import re

    m = re.search(r"(\d{4}-\d{4}-\d{4}-\d{3}[\dX])", raw)
    return m.group(1) if m else None


# Force `Any` import to keep type-only usage explicit and avoid linter noise
_ = Any
