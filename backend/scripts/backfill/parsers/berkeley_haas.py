"""Parser for `haas.berkeley.edu/faculty/{slug}/` profile pages.

Haas uses a WordPress-style theme with semantic class names but scatter-shot
markup. Layout:

    <h1 class="entry-title title-3">Firstname Lastname</h1>
    <section class="entry-content" itemprop="description text">
      <section class="post-content">
        <p class="intro-text"><strong>{title | title | title}</strong>
          <br />{academic_area}</p>
        <p>{first bio paragraph}</p>
        ...
        <ul class="social-media">
          <li><a href="https://personal-site" ...>Personal Website</a></li>
          <li><a href="mailto:..." /></li>
        </ul>
      </section>
    </section>

Titles come pipe-separated inside the <strong> tag. We collapse the pipes to
` · ` for consistency with the other parsers.
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

_INTRO_RE = re.compile(
    r'<p class="intro-text">\s*<strong>(?P<titles>.*?)</strong>',
    re.S | re.I,
)

# Personal-site link is inside <ul class="social-media"> with anchor text
# "Personal Website" (or a close variant). Whitespace tolerant.
_WEBSITE_RE = re.compile(
    r'<a[^>]+href="(?P<href>https?://[^"]+)"[^>]*>\s*'
    r'[^<]*?(?:Personal Website|Website|Homepage)[^<]*?\s*</a>',
    re.I,
)

# The bio paragraph is the first substantive <p> inside the .post-content
# region that isn't the intro-text paragraph itself. We rely on the ordering:
# the intro-text closes first, then the bio paragraph opens.
_POST_CONTENT_RE = re.compile(
    r'<section class="post-content">(?P<inner>.*?)</section>',
    re.S | re.I,
)
_PARAGRAPH_RE = re.compile(r'<p(?![^>]*class="intro-text")[^>]*>(?P<body>.*?)</p>', re.S | re.I)


def parse(url: str, html: str) -> ProfileExtraction:
    out = ProfileExtraction()

    intro = _INTRO_RE.search(html)
    if intro:
        raw = unescape(intro.group("titles"))
        title = _normalize_title_pipes(raw)
        if title:
            out.title = title

    web = _WEBSITE_RE.search(html)
    if web:
        href = web.group("href").strip()
        if _plausible_personal_site(href):
            out.personal_url = href

    post = _POST_CONTENT_RE.search(html)
    if post:
        for p in _PARAGRAPH_RE.finditer(post.group("inner")):
            text = _bio_text(p.group("body"))
            if _looks_like_bio(text):
                out.biography = text
                break

    orcid = canonicalize_orcid(html)
    if orcid:
        out.orcid = orcid

    if not out.title and not out.biography:
        out.notes.append("berkeley_haas: nothing extracted")

    return out


def _normalize_title_pipes(raw: str) -> str:
    """Titles arrive as `A | B | C`. Collapse to `A · B · C` and drop any
    residual HTML tags inside the <strong>."""
    without_tags = re.sub(r"<[^>]+>", " ", raw)
    without_tags = unescape(without_tags)
    parts = [p.strip() for p in without_tags.split("|") if p.strip()]
    return " · ".join(parts)


def _bio_text(fragment: str) -> str:
    with_breaks = re.sub(r"<br\s*/?>", "\n", fragment, flags=re.I)
    return normalize_whitespace(strip_html(with_breaks))


def _looks_like_bio(text: str) -> bool:
    if not text or len(text) < 60:
        return False
    lowered = text.lower()
    bad_markers = ("phone number", "email ", "click here", "download cv")
    if any(m in lowered for m in bad_markers):
        return False
    return True


def _plausible_personal_site(href: str) -> bool:
    """Reject only links that point back to another Haas *profile* page.

    Faculty personal sites frequently live on the `faculty.haas.berkeley.edu`
    subdomain (e.g. `https://faculty.haas.berkeley.edu/hermalin/`), and those
    are exactly the URLs we want, so we don't blanket-reject the parent domain.
    """
    if "haas.berkeley.edu/faculty/" in href:
        return False
    if any(
        bad in href
        for bad in (
            "twitter.com",
            "facebook.com",
            "linkedin.com",
            "instagram.com",
            "youtube.com",
            "/cdn-cgi/l/email-protection",
        )
    ):
        return False
    return True
