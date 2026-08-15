"""Parser for `econ.berkeley.edu/profile/{slug}` profile pages.

Berkeley Econ uses distinct semantic class names for each field, so extraction
is cleaner than Stanford Econ:

    <h1 class="display-name">Edlin, Aaron</h1>          — "Last, First" order
    <div class="display-position">…</div>               — title
    <div class="display-homepage">…<a href="…">…</a>    — personal site
    <div class="display-cv">…<a href="…">…</a>          — CV PDF (deferred)
    <div class="field--name-field-bio">…<div class="field__item">bio</div>…</div>

Extraction contract matches `ProfileExtraction` in scripts.backfill.common.
"""

from __future__ import annotations

import re
from html import unescape

from scripts.backfill.common import (
    ProfileExtraction,
    canonicalize_orcid,
    normalize_whitespace,
    strip_html,
)

_POSITION_RE = re.compile(
    r'<div class="display-position">(?P<t>[^<]*)</div>', re.IGNORECASE
)

_HOMEPAGE_RE = re.compile(
    r'<div class="display-homepage">\s*<div class="field_value">\s*'
    r'<a[^>]+href="(?P<href>https?://[^"]+)"',
    re.IGNORECASE | re.DOTALL,
)

# The bio lives inside `field--name-field-bio` → `field__item`. We accept
# either quote style and any attribute order because Drupal sometimes reshuffles
# attributes between renders.
_BIO_RE = re.compile(
    r'field--name-field-bio[^>]*>.*?'
    r'<div class="field__item">(?P<bio>.*?)</div>',
    re.IGNORECASE | re.DOTALL,
)


def parse(url: str, html: str) -> ProfileExtraction:
    out = ProfileExtraction()

    pos = _POSITION_RE.search(html)
    if pos:
        title = unescape(pos.group("t")).strip()
        if title:
            out.title = title

    home = _HOMEPAGE_RE.search(html)
    if home:
        href = home.group("href").strip()
        if _plausible_personal_site(href):
            out.personal_url = href

    bio_match = _BIO_RE.search(html)
    if bio_match:
        bio_text = _bio_text(bio_match.group("bio"))
        if bio_text and len(bio_text) >= 30:
            out.biography = bio_text

    orcid = canonicalize_orcid(html)
    if orcid:
        out.orcid = orcid

    if not out.title and not out.biography:
        out.notes.append("berkeley_econ: nothing extracted")

    return out


def _bio_text(fragment: str) -> str:
    with_breaks = re.sub(r"</p\s*>", "\n\n", fragment, flags=re.IGNORECASE)
    with_breaks = re.sub(r"<br\s*/?>", "\n", with_breaks, flags=re.IGNORECASE)
    return normalize_whitespace(strip_html(with_breaks))


def _plausible_personal_site(href: str) -> bool:
    return not any(
        bad in href
        for bad in (
            "econ.berkeley.edu",  # linking back to the same profile page
            "twitter.com",
            "facebook.com",
            "linkedin.com",
            "youtube.com",
        )
    )
