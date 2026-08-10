"""Thin OpenAlex API client for publication backfill.

Authenticates with ``OPENALEX_API_KEY`` (free tier: ~$1/day budget). Set it in
``.env`` or the environment before running ``scripts.backfill.publications``.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from db.config import load_dotenv

OPENALEX_BASE = "https://api.openalex.org"
DEFAULT_TIMEOUT = 30.0
MIN_INTERVAL = 0.11


def _user_agent() -> str:
    """Polite-pool User-Agent.  OpenAlex asks for a contact email — override
    with OPENALEX_CONTACT_EMAIL (the original author's address is the default
    so nothing breaks for existing deployments)."""
    contact = os.environ.get("OPENALEX_CONTACT_EMAIL") or "chendyu@berkeley.edu"
    return f"elmonte-backfill/0.2 (research directory; contact: {contact})"


USER_AGENT = _user_agent()

# Work types we ingest for faculty bibliographies. Excludes book chapters, datasets,
# reference entries, etc. NBER working papers are usually `report` in OpenAlex.
DEFAULT_WORK_TYPES: tuple[str, ...] = (
    "article",
    "report",
    "preprint",
    "conference-paper",
    "review",
)

# Short OpenAlex institution ids for roster hosts we ingest today.
INSTITUTION_LINEAGE: dict[str, str] = {
    "stanford": "I97018004",
    "berkeley": "I95457486",
    "mit": "I63966007",
    "harvard": "I136199984",
    "yale": "I32971472",
    "uchicago": "I40347166",
    "princeton": "I20089843",
}


def resolve_api_key(explicit: str | None = None) -> str:
    load_dotenv()
    return (explicit or os.environ.get("OPENALEX_API_KEY") or "").strip()


def short_id(openalex_id: str | None) -> str | None:
    """`https://openalex.org/A123` → `A123`."""
    if not openalex_id:
        return None
    return openalex_id.rstrip("/").rsplit("/", 1)[-1]


class OpenAlexClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        min_interval: float | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = resolve_api_key(api_key)
        # Anonymous tier is rate-limited harder; be polite by default.
        self.min_interval = min_interval or (MIN_INTERVAL if self.api_key else 0.35)
        self.timeout = timeout
        self._last_hit = 0.0

    def _wait(self) -> None:
        gap = time.monotonic() - self._last_hit
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)

    def _with_api_key(self, url: str) -> str:
        if not self.api_key:
            return url
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        qs["api_key"] = [self.api_key]
        query = urllib.parse.urlencode(qs, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=query))

    def get_json(self, path_or_url: str, *, params: dict[str, str] | None = None) -> dict[str, Any]:
        if path_or_url.startswith("http"):
            url = path_or_url
        else:
            url = f"{OPENALEX_BASE}{path_or_url}"
        if params:
            sep = "&" if "?" in url else "?"
            url = f"{url}{sep}{urllib.parse.urlencode(params)}"
        url = self._with_api_key(url)

        backoff = 2.0
        for attempt in range(8):
            self._wait()
            req = urllib.request.Request(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": "application/json",
                },
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    body = resp.read()
                    status = int(resp.status)
            except urllib.error.HTTPError as err:
                body = err.read() or b""
                status = int(err.code)
            except urllib.error.URLError as err:
                if attempt < 7:
                    time.sleep(backoff)
                    backoff = min(backoff * 2, 120.0)
                    continue
                raise RuntimeError(f"OpenAlex network error for {url}: {err}") from err
            finally:
                self._last_hit = time.monotonic()

            if status == 429 and attempt < 7:
                time.sleep(backoff)
                backoff = min(backoff * 2, 120.0)
                continue
            if status >= 400:
                raise RuntimeError(f"OpenAlex {status} for {url}: {body[:200]!r}")
            return json.loads(body)

        raise RuntimeError(f"OpenAlex rate limit persisted for {url}")

    def author_by_orcid(self, orcid: str) -> dict[str, Any] | None:
        try:
            return self.get_json(f"/authors/orcid:{orcid}")
        except RuntimeError:
            return None

    def author_by_id(self, author_id: str) -> dict[str, Any] | None:
        aid = short_id(author_id)
        if not aid:
            return None
        try:
            return self.get_json(f"/authors/{aid}")
        except RuntimeError:
            return None

    def institution_by_id(self, institution_id: str) -> dict[str, Any] | None:
        iid = short_id(institution_id)
        if not iid:
            return None
        try:
            return self.get_json(f"/institutions/{iid}")
        except RuntimeError:
            return None

    def search_authors(
        self,
        display_name: str,
        *,
        institution_hint: str | None = None,
        per_page: int = 5,
    ) -> list[dict[str, Any]]:
        params: dict[str, str] = {
            "search": display_name,
            "per-page": str(per_page),
        }
        if institution_hint:
            lineage = INSTITUTION_LINEAGE.get(institution_hint.lower())
            if lineage:
                params["filter"] = f"last_known_institutions.lineage:{lineage}"
        data = self.get_json("/authors", params=params)
        return list(data.get("results") or [])

    def works_for_author(
        self,
        author_id: str,
        *,
        max_works: int = 150,
        per_page: int = 200,
        min_year: int | None = None,
        work_types: tuple[str, ...] | None = DEFAULT_WORK_TYPES,
        exclude_paratext: bool = True,
    ) -> list[dict[str, Any]]:
        aid = short_id(author_id)
        if not aid:
            return []

        works: list[dict[str, Any]] = []
        cursor = "*"
        while cursor and len(works) < max_works:
            filt_parts = [f"authorships.author.id:{aid}"]
            if min_year is not None:
                filt_parts.append(f"publication_year:{min_year}-")
            if work_types:
                filt_parts.append(f"type:{'|'.join(work_types)}")
            if exclude_paratext:
                filt_parts.append("is_paratext:false")
            filt = ",".join(filt_parts)
            params = {
                "filter": filt,
                "per-page": str(min(per_page, max_works - len(works))),
                "cursor": cursor,
            }
            data = self.get_json("/works", params=params)
            batch = list(data.get("results") or [])
            works.extend(batch)
            cursor = data.get("meta", {}).get("next_cursor")
            if not batch:
                break
        return works[:max_works]


def pick_best_author(
    candidates: list[dict[str, Any]],
    *,
    display_name: str,
    institution_hint: str | None,
) -> dict[str, Any] | None:
    if not candidates:
        return None

    target = display_name.strip().lower()
    hint = (institution_hint or "").strip().lower()

    def score(author: dict[str, Any]) -> float:
        name = (author.get("display_name") or "").strip().lower()
        s = 0.0
        if name == target:
            s += 20.0
        elif target in name or name in target:
            s += 8.0
        else:
            target_parts = set(target.split())
            name_parts = set(name.split())
            s += len(target_parts & name_parts) * 3.0

        if hint:
            for inst in author.get("last_known_institutions") or []:
                inst_name = (inst.get("display_name") or "").lower()
                if hint in inst_name:
                    s += 12.0
                    break

        s += min(float(author.get("works_count") or 0) / 50.0, 4.0)
        return s

    ranked = sorted(candidates, key=score, reverse=True)
    best = ranked[0]
    if score(best) < 8.0:
        return None
    return best


def institution_hint_from_url(url: str | None) -> str | None:
    if not url:
        return None
    host = urllib.parse.urlparse(url).netloc.lower()
    if "stanford" in host:
        return "stanford"
    if "berkeley" in host:
        return "berkeley"
    return None
