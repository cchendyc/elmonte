"""Parser for `www.gsb.stanford.edu/faculty-research/faculty/{slug}` pages.

GSB uses distinct semantic class names on a Drupal-derived template:

    <h1 class="c-node-faculty">Firstname Lastname</h1>
    <div class="c-node-faculty__endowed-title">Professor of ...</div>
    <div class="c-node-faculty__jaorhfp-title">Endowed Chair ...</div>   (0+)
    <div class="bio-content ...">... bio HTML ...</div>

GSB does NOT expose personal-website links on the profile pages we've seen,
so we extract only when one appears; `homepage_url` coverage will be near zero
for this host. Bio and title coverage should be near 100%.

The bio div uses a "show more" toggle in the UI, so its inner HTML often
contains the full text plus paginated markers. We take everything inside the
outermost `<div class="bio-content ...">` block and let `strip_html` collapse
whitespace.
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

_TITLE_ROW_RE = re.compile(
    r'<div\s+class="c-node-faculty__(?:endowed-title|jaorhfp-title)[^"]*"[^>]*>\s*'
    r"(?P<t>[^<]+?)\s*</div>",
    re.I,
)

_META_DESC_RE = re.compile(
    r'<meta\s+name="description"\s+content="([^"]+)"', re.I
)

_BIO_RE = re.compile(
    r'<div\s+class="bio-content[^"]*"[^>]*>(?P<inner>.*?)</div>\s*</div>',
    re.S | re.I,
)

# Long shot: some GSB pages carry a Google Scholar or personal-site link in
# the "External links" region. Detect them if present but don't rely on it.
_EXTERNAL_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>https?://[^"]+)"[^>]*>\s*'
    r"[^<]*?(?:Personal Website|Homepage|Faculty Website)[^<]*?\s*</a>",
    re.I,
)


def parse(url: str, html: str) -> ProfileExtraction:
    out = ProfileExtraction()

    titles = [
        unescape(m.group("t")).strip()
        for m in _TITLE_ROW_RE.finditer(html)
    ]
    titles = [t for t in titles if t]
    if titles:
        out.title = " · ".join(titles)

    bio_match = _BIO_RE.search(html)
    if bio_match:
        text = _bio_text(bio_match.group("inner"))
        if text and len(text) >= 30:
            out.biography = text
    if out.biography is None:
        meta = _META_DESC_RE.search(html)
        if meta:
            desc = unescape(meta.group(1)).strip()
            if len(desc) >= 30:
                out.biography = desc

    web = _EXTERNAL_LINK_RE.search(html)
    if web:
        href = web.group("href").strip()
        if _plausible_personal_site(href):
            out.personal_url = href

    orcid = canonicalize_orcid(html)
    if orcid:
        out.orcid = orcid

    if not out.title and not out.biography:
        out.notes.append("stanford_gsb: nothing extracted")

    return out


def _bio_text(fragment: str) -> str:
    with_breaks = re.sub(r"</p\s*>", "\n\n", fragment, flags=re.I)
    with_breaks = re.sub(r"<br\s*/?>", "\n", with_breaks, flags=re.I)
    return normalize_whitespace(strip_html(with_breaks))


def _plausible_personal_site(href: str) -> bool:
    return not any(
        bad in href
        for bad in (
            "gsb.stanford.edu",
            "stanford.edu/email-contact",
            "twitter.com",
            "facebook.com",
            "linkedin.com",
            "youtube.com",
            "instagram.com",
        )
    )
