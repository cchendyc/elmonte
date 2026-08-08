"""Parser for `economics.stanford.edu/people/{slug}` profile pages.

The page is Drupal 11 with a consistent set of CSS classes that hang off a
`hb-three-column-w-image__main-body` container. We scope every extraction to
that container so we don't accidentally pick up sidebar/footer text that
shares the generic `body text-with-summary` class.

Extraction contract:
    title         → concatenated role lines from `field-hs-person-title`
                    (only when present; some pages have empty title blocks)
    biography     → text of the first `body text-with-summary` div inside the
                    main content region, or the `<meta name="description">`
                    content as a fallback. If neither exists, None.
    orcid         → first canonical-shape ORCID string found anywhere. Rare.
    personal_url  → the `<a>` labelled "{Lastname} website" — the recurring
                    Stanford Econ pattern for the researcher's own site.
    publications  → empty for now. Stanford Econ pages don't list publications
                    in a structured way. OpenAlex will backfill this later.
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

_MAIN_BODY_RE = re.compile(
    r'<div class="hb-three-column-w-image__main-body[^"]*">(?P<main>.*?)(?:'
    r"<footer|<aside|<div class=\"hb-related-content|$)",
    re.S | re.I,
)

# The title block wraps 1-3 `<div>{role}</div>` lines. We use a lookahead to
# terminate the capture at the *next* Drupal block (`<div class="...`) rather
# than at a closing `</div>`, since counting matched tags with a regex is
# unreliable when there are multiple sibling `<div>` inside the block.
_TITLE_BLOCK_RE = re.compile(
    r'<div class="hs-font-lead field-hs-person-title[^"]*">'
    r"(?P<inner>.*?)"
    r'(?=<div class="|<footer|</body)',
    re.S | re.I,
)
_TITLE_LINE_RE = re.compile(r"<div>\s*([^<][^<]*?)\s*</div>", re.S)

_BODY_RE = re.compile(
    r'<div class="body text-with-summary[^"]*">(?P<inner>.*?)</div>',
    re.S | re.I,
)

_WEBSITE_LINK_RE = re.compile(
    r'<a[^>]+href="(?P<href>https?://[^"]+)"[^>]*>\s*(?P<text>[^<]*?website[^<]*?)\s*</a>',
    re.I,
)

_META_DESC_RE = re.compile(
    r'<meta\s+name="description"\s+content="([^"]+)"', re.I
)


def parse(url: str, html: str) -> ProfileExtraction:
    out = ProfileExtraction()

    main_match = _MAIN_BODY_RE.search(html)
    main_region = main_match.group("main") if main_match else html

    # Title block lives in the top-header region above `main_region`, so
    # search the full document. Biography is scoped to `main_region` below
    # to avoid picking up sidebar/footer text with the same CSS class.
    title_match = _TITLE_BLOCK_RE.search(html)
    if title_match:
        lines = [
            unescape(line).strip()
            for line in _TITLE_LINE_RE.findall(title_match.group("inner"))
        ]
        lines = [line for line in lines if line]
        if lines:
            out.title = " · ".join(lines)

    body_match = _BODY_RE.search(main_region)
    if body_match:
        text = _bio_text(body_match.group("inner"))
        if _looks_like_bio(text):
            out.biography = text

    if out.biography is None:
        meta_match = _META_DESC_RE.search(html)
        if meta_match:
            desc = unescape(meta_match.group(1)).strip()
            if _looks_like_bio(desc):
                out.biography = desc

    for m in _WEBSITE_LINK_RE.finditer(html):
        href = m.group("href").strip()
        if _plausible_personal_site(href):
            out.personal_url = href
            break

    orcid = canonicalize_orcid(html)
    if orcid:
        out.orcid = orcid

    if not out.biography and not out.title:
        out.notes.append("stanford_econ: no title or bio found")

    return out


def _bio_text(html_fragment: str) -> str:
    """Convert an HTML bio blob to plain text, preserving paragraph breaks."""
    with_breaks = re.sub(r"</p\s*>", "\n\n", html_fragment, flags=re.I)
    with_breaks = re.sub(r"<br\s*/?>", "\n", with_breaks, flags=re.I)
    return normalize_whitespace(strip_html(with_breaks))


def _looks_like_bio(text: str | None) -> bool:
    """Heuristic to reject boilerplate contact/footer blobs that share the
    same CSS class as real bios.

    Real bios almost always contain either the person's name (which we don't
    know here) or biographical prose (long enough to be > ~30 chars and free of
    obvious form/directory markers).
    """
    if not text:
        return False
    t = text.strip()
    if len(t) < 30:
        return False
    lowered = t.lower()
    bad_markers = (
        "[at]",
        "phone :",
        "campus map",
        "connect with us",
        "job market",
        "admissions",
        "peer advisors",
    )
    if any(marker in lowered for marker in bad_markers):
        return False
    return True


def _plausible_personal_site(href: str) -> bool:
    """Filter out obvious non-personal links that happen to have 'website' in
    the link text (mailing list managers, journal sites, etc.)."""
    return not any(
        bad in href
        for bad in (
            "twitter.com",
            "facebook.com",
            "linkedin.com",
            "youtube.com",
            "mailchimp",
            "listserv",
        )
    )
