"""Parse employment and education history from CV text."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime

from scripts.backfill.common import (
    CvAffiliationCandidate,
    normalize_whitespace,
    strip_html,
    utc_year_end,
    utc_year_start,
)
from scripts.backfill.rank import classify_position_rank, infer_affiliation_kind

_SECTION_EMPLOYMENT_RE = re.compile(
    r"^(?:employment|appointments|academic appointments|academic positions|"
    r"professional experience|positions held|positions|experience|work history)\s*$",
    re.I,
)
_SECTION_EDUCATION_RE = re.compile(
    r"^(?:education|degrees|academic background|training)\s*$",
    re.I,
)
_SECTION_STOP_RE = re.compile(
    r"^(?:publications|working papers|journal publications|research|awards|"
    r"honors|grants|teaching|references|other publications|under review)\b",
    re.I,
)

_LEADING_ENTRY_RE = re.compile(
    r"^(?P<start>(?:19|20)\d{2})\s*[-–—]\s*"
    r"(?:(?P<end>(?:19|20)\d{2}|present|current|now)\s+)?"
    r"(?P<body>.+)$",
    re.I,
)
_INLINE_DATE_RE = re.compile(
    r"(?P<start>(?:19|20)\d{2})\s*[-–—]\s*"
    r"(?P<end>(?:19|20)\d{2}|present|current|now)",
    re.I,
)
_SINGLE_YEAR_RE = re.compile(r"\b((?:19|20)\d{2})\b")
_ROLE_RE = re.compile(
    r"\b("
    r"(?:Visiting\s+)?(?:Assistant|Associate)\s+Professor(?:\s+of\s+[^,;]+)?|"
    r"Professor(?:\s+Emeritus|\s+of\s+[^,;]+)?|"
    r"Postdoctoral(?:\s+Research)?\s+Fellow|"
    r"Research\s+Affiliate|Affiliate\s+Member|"
    r"Ph\.?D\.?(?:\s+in\s+[^,;]+)?|"
    r"A\.?B\.?(?:\s+in\s+[^,;]+)?|"
    r"M\.?A\.?(?:\s+in\s+[^,;]+)?"
    r")\b",
    re.I,
)
_ORG_MARKERS = (
    "university",
    "college",
    "institute",
    "school",
    "department",
    "laboratory",
    "faculty",
    "gsb",
    "haas",
    "economics",
    "business",
    "center",
    "centre",
)

_SECTION_HEADERS = (
    "Academic Positions",
    "Education",
    "Employment",
    "Appointments",
    "Professional Experience",
    "Degrees",
    "Journal Publications",
    "Working Papers",
    "Publications",
)


class CvTextExtractionError(Exception):
    """Raised when a CV response cannot be turned into plain text."""


def is_pdf_body(body: bytes) -> bool:
    return body.lstrip()[:4] == b"%PDF"


def is_html_body(body: bytes) -> bool:
    head = body.lstrip()[:32].lower()
    return head.startswith((b"<!doctype", b"<html", b"<?xml"))


def extract_text_from_pdf(body: bytes) -> str:
    from io import BytesIO

    from pypdf import PdfReader
    from pypdf.errors import PdfReadError, PdfStreamError

    try:
        reader = PdfReader(BytesIO(body), strict=False)
    except (PdfReadError, PdfStreamError, OSError, ValueError) as err:
        raise CvTextExtractionError(f"pdf_read_failed: {err}") from err

    parts: list[str] = []
    for page in reader.pages:
        try:
            text = page.extract_text() or ""
        except (PdfReadError, PdfStreamError, OSError, ValueError):
            continue
        if text.strip():
            parts.append(text)

    if not parts:
        raise CvTextExtractionError("pdf_no_extractable_text")
    return preprocess_cv_text("\n".join(parts))


def preprocess_cv_text(text: str) -> str:
    """Turn PDF blobs into one-entry-per-line text where possible."""
    text = unicodedata.normalize("NFKC", text)
    text = text.replace("\u2013", "–").replace("\u2014", "–")

    for header in _SECTION_HEADERS:
        text = re.sub(
            rf"\s+({re.escape(header)})\s+",
            rf"\n\1\n",
            text,
            flags=re.I,
        )

    text = re.sub(r"(?<!\d)(\d{4}\s*[-–—])", r"\n\1", text)
    lines = [normalize_whitespace(line) for line in text.splitlines()]
    return "\n".join(line for line in lines if line)


def cv_text_from_body(url: str, body: bytes, content_type: str | None = None) -> str:
    lowered_type = (content_type or "").lower()

    if is_pdf_body(body):
        return extract_text_from_pdf(body)
    if is_html_body(body) or "html" in lowered_type:
        return preprocess_cv_text(strip_html(body.decode(errors="replace")))

    lowered_url = url.lower()
    if lowered_url.endswith(".pdf") or "pdf" in lowered_type:
        try:
            return extract_text_from_pdf(body)
        except CvTextExtractionError:
            if is_html_body(body):
                return preprocess_cv_text(strip_html(body.decode(errors="replace")))
            raise

    return preprocess_cv_text(strip_html(body.decode(errors="replace")))


def parse_cv_text(text: str) -> list[CvAffiliationCandidate]:
    lines = [normalize_whitespace(line) for line in text.splitlines()]
    lines = [line for line in lines if line]

    section = "employment"
    entries: list[CvAffiliationCandidate] = []
    seen: set[tuple[str, str | None, str | None]] = set()

    for line in lines:
        if _SECTION_EMPLOYMENT_RE.match(line):
            section = "employment"
            continue
        if _SECTION_EDUCATION_RE.match(line):
            section = "education"
            continue
        if _SECTION_STOP_RE.match(line):
            break

        candidate = _parse_line(line, section=section)
        if candidate is None:
            continue

        key = (
            candidate.title.lower(),
            candidate.starts_at.isoformat() if candidate.starts_at else None,
            candidate.ends_at.isoformat() if candidate.ends_at else None,
        )
        if key in seen:
            continue
        seen.add(key)
        entries.append(candidate)

    return entries


def _parse_line(line: str, *, section: str) -> CvAffiliationCandidate | None:
    leading = _LEADING_ENTRY_RE.match(line)
    if leading:
        starts_at, ends_at = _parse_year_range(
            leading.group("start"), leading.group("end") or "present"
        )
        return _candidate_from_body(
            leading.group("body"),
            section=section,
            starts_at=starts_at,
            ends_at=ends_at,
        )

    inline = _INLINE_DATE_RE.search(line)
    if inline:
        starts_at, ends_at = _parse_year_range(
            inline.group("start"), inline.group("end")
        )
        body = (line[: inline.start()] + line[inline.end() :]).strip(" ,;–-")
        if len(body) >= 4:
            return _candidate_from_body(
                body, section=section, starts_at=starts_at, ends_at=ends_at
            )

    if section != "education":
        return None

    years = [int(y) for y in _SINGLE_YEAR_RE.findall(line)]
    if not years:
        return None
    year = years[-1]
    body = _SINGLE_YEAR_RE.sub("", line).strip(" ,;()–-")
    if len(body) < 4:
        return None
    return _candidate_from_body(
        body,
        section=section,
        starts_at=utc_year_start(year),
        ends_at=utc_year_end(year),
    )


def _parse_year_range(start: str, end: str) -> tuple[datetime, datetime | None]:
    starts_at = utc_year_start(int(start))
    if end.lower() in {"present", "current", "now"}:
        return starts_at, None
    return starts_at, utc_year_end(int(end))


def _candidate_from_body(
    body: str,
    *,
    section: str,
    starts_at: datetime | None,
    ends_at: datetime | None,
) -> CvAffiliationCandidate | None:
    body = body.strip(" .;")
    if len(body) < 4:
        return None

    title, organization = _split_title_org(body)
    if not title:
        return None

    return CvAffiliationCandidate(
        title=title,
        organization=organization,
        affiliation_kind=infer_affiliation_kind(title, section=section),
        position_rank=classify_position_rank(title),
        starts_at=starts_at,
        ends_at=ends_at,
    )


def _split_title_org(text: str) -> tuple[str, str]:
    title, organization = _split_role_from_org_blob(text)
    if organization:
        return title, organization

    parts = [part.strip() for part in re.split(r"\s{2,}|\s*,\s*", text) if part.strip()]
    if len(parts) == 1:
        return _split_role_from_org_blob(parts[0])

    org_index = _best_org_index(parts)
    if org_index is None:
        return _split_role_from_org_blob(text)

    title_parts = [part for idx, part in enumerate(parts) if idx != org_index]
    title = ", ".join(title_parts) if title_parts else parts[0]
    organization = parts[org_index]
    if title.lower() == organization.lower():
        return _split_role_from_org_blob(organization)
    return title, organization


def _split_role_from_org_blob(text: str) -> tuple[str, str]:
    role_match = _ROLE_RE.search(text)
    if role_match:
        organization = text[: role_match.start()].strip(" ,;")
        title = text[role_match.start() :].strip(" ,;")
        if title and organization:
            return title, organization

    lowered = text.lower()
    for marker in _ORG_MARKERS:
        idx = lowered.find(marker)
        if idx <= 0:
            continue
        organization = text[:idx].strip(" ,;")
        title = text[idx:].strip(" ,;")
        if title and organization:
            return title, organization
    return text, ""


def _best_org_index(parts: list[str]) -> int | None:
    for idx in range(len(parts) - 1, -1, -1):
        lowered = parts[idx].lower()
        if any(marker in lowered for marker in _ORG_MARKERS):
            return idx
    return len(parts) - 1 if len(parts) > 1 else None
