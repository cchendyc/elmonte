"""Discover a CV URL on a researcher's personal homepage."""

from __future__ import annotations

import re
from html import unescape
from urllib.parse import urljoin, urlparse

from scripts.backfill.common import normalize_whitespace, strip_html

_CV_HREF_RE = re.compile(
    r'<a[^>]+href=["\'](?P<href>[^"\']+)["\'][^>]*>(?P<label>.*?)</a>',
    re.I | re.S,
)

_CV_LABEL_RE = re.compile(
    r"\b(?:curriculum\s+vitae|vitae|resume|resumé|cv)\b",
    re.I,
)

_CV_PATH_RE = re.compile(
    r"(?:^|[/?#])(?:cv|curriculum[-_]?vitae|vita)(?:\.(?:pdf|docx?|html?))?(?:$|[/?#])",
    re.I,
)


def discover_cv_url(homepage_url: str, html: str) -> str | None:
    """Return the best CV link found on `homepage_url`, or None."""
    candidates: list[tuple[int, str]] = []

    for match in _CV_HREF_RE.finditer(html):
        href = unescape(match.group("href")).strip()
        if not href or href.startswith(("#", "mailto:", "javascript:")):
            continue
        absolute = urljoin(homepage_url, href)
        if not _plausible_cv_url(absolute, homepage_url):
            continue

        label = normalize_whitespace(strip_html(match.group("label")))
        score = _score_candidate(absolute, label)
        if score > 0:
            candidates.append((score, absolute))

    if not candidates:
        return None

    candidates.sort(key=lambda item: (-item[0], len(item[1])))
    return candidates[0][1]


def _score_candidate(url: str, label: str) -> int:
    score = 0
    lowered_url = url.lower()
    lowered_label = label.lower()

    if lowered_url.endswith(".pdf"):
        score += 5
    if _CV_PATH_RE.search(lowered_url):
        score += 4
    if _CV_LABEL_RE.search(lowered_label):
        score += 4
    if "download" in lowered_label:
        score += 1
    if "cv" == lowered_label.strip():
        score += 2
    if any(bad in lowered_url for bad in ("linkedin.com", "twitter.com", "facebook.com")):
        return 0
    return score


def _plausible_cv_url(url: str, homepage_url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False

    lowered = url.lower()
    if _CV_PATH_RE.search(lowered):
        return True
    if lowered.endswith((".pdf", ".doc", ".docx")) and "cv" in lowered:
        return True

    home_host = urlparse(homepage_url).netloc.lower()
    host = parsed.netloc.lower()
    if host and host != home_host and not host.endswith("." + home_host):
        # Allow common file hosts used by academics.
        if not any(
            allowed in host
            for allowed in ("dropbox.com", "google.com", "github.io", "github.com")
        ):
            return False
    return "cv" in lowered or "vitae" in lowered or "resume" in lowered
