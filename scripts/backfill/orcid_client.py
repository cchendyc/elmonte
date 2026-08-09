"""ORCID public API client."""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

ORCID_PUBLIC_BASE = "https://pub.orcid.org/v3.0"
USER_AGENT = "elmonte-backfill/0.1 (research directory; contact: chendyu@berkeley.edu)"
DEFAULT_TIMEOUT = 30.0
MIN_INTERVAL = 0.5


def _escape_solr(value: str) -> str:
    cleaned = value.strip()
    if not cleaned:
        return '""'
    if any(ch in cleaned for ch in ' \t"()'):
        escaped = cleaned.replace('"', '\\"')
        return f'"{escaped}"'
    return cleaned


class OrcidClient:
    def __init__(
        self,
        *,
        min_interval: float = MIN_INTERVAL,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.min_interval = min_interval
        self.timeout = timeout
        self._last_hit = 0.0

    def _wait(self) -> None:
        gap = time.monotonic() - self._last_hit
        if gap < self.min_interval:
            time.sleep(self.min_interval - gap)

    def _get_json(self, url: str) -> tuple[int, dict[str, Any] | None]:
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
        finally:
            self._last_hit = time.monotonic()

        if status >= 400:
            return status, None
        return status, json.loads(body)

    def fetch_record(self, orcid: str) -> dict[str, Any] | None:
        status, data = self._get_json(f"{ORCID_PUBLIC_BASE}/{orcid}/record")
        if status >= 400 or data is None:
            return None
        return data

    def search(
        self,
        *,
        firstname: str,
        lastname: str,
        affiliation: str | None = None,
        rows: int = 5,
    ) -> list[dict[str, Any]]:
        """Search the ORCID registry by name and optional affiliation."""
        parts = [
            f"family-name:{_escape_solr(lastname)}",
            f"given-names:{_escape_solr(firstname.split()[0])}",
        ]
        if affiliation:
            parts.append(f"affiliation-org-name:{_escape_solr(affiliation)}")

        query = " AND ".join(parts)
        params = urllib.parse.urlencode({"q": query, "rows": str(rows)})
        status, data = self._get_json(f"{ORCID_PUBLIC_BASE}/expanded-search/?{params}")
        if status >= 400 or data is None:
            return []
        return list(data.get("expanded-result") or [])


def affiliation_hints_from_org(
    org_name: str | None,
    org_short_name: str | None,
) -> list[str]:
    """Build ORCID affiliation search terms from a roster organization."""
    hints: list[str] = []
    for raw in (org_name, org_short_name):
        if not raw or not raw.strip():
            continue
        hints.append(raw.strip())

    combined = " ".join(hints).lower()
    if "berkeley" in combined:
        hints.extend(
            [
                "University of California Berkeley",
                "UC Berkeley",
                "Berkeley",
            ]
        )
    if "stanford" in combined:
        hints.extend(["Stanford University", "Stanford"])

    seen: set[str] = set()
    ordered: list[str] = []
    for hint in hints:
        key = hint.lower()
        if key in seen:
            continue
        seen.add(key)
        ordered.append(hint)
    return ordered


def affiliation_hint_from_profile_url(url: str | None) -> str | None:
    if not url:
        return None
    host = urllib.parse.urlparse(url).netloc.lower()
    if "berkeley" in host:
        return "Berkeley"
    if "stanford" in host:
        return "Stanford"
    return None


def pick_best_orcid(
    candidates: list[dict[str, Any]],
    *,
    firstname: str,
    lastname: str,
    affiliation_hints: list[str] | None = None,
) -> dict[str, Any] | None:
    if not candidates:
        return None

    target_last = lastname.strip().lower()
    target_first = firstname.strip().lower().split()[0]
    hints = [h.strip().lower() for h in (affiliation_hints or []) if h and h.strip()]

    def institution_score(item: dict[str, Any]) -> float:
        institutions = [str(inst).lower() for inst in item.get("institution-name") or []]
        if not hints or not institutions:
            return 0.0
        best = 0.0
        for hint in hints:
            for inst in institutions:
                if hint in inst or inst in hint:
                    best = max(best, 14.0 if len(hint) > 10 else 10.0)
        return best

    def score(item: dict[str, Any]) -> float:
        family = (item.get("family-names") or "").strip().lower()
        given = (item.get("given-names") or "").strip().lower()
        points = 0.0
        if family == target_last:
            points += 20.0
        elif target_last in family:
            points += 8.0
        if given == target_first or given.startswith(f"{target_first} "):
            points += 15.0
        elif given.split() and given.split()[0] == target_first:
            points += 10.0
        points += institution_score(item)
        return points

    ranked = sorted(candidates, key=score, reverse=True)
    best = ranked[0]
    best_score = score(best)
    if best_score < 20.0:
        return None

    if len(ranked) > 1:
        second_score = score(ranked[1])
        if best_score - second_score < 3.0 and institution_score(best) <= 0:
            return None
    return best


def search_orcid_candidates(
    client: OrcidClient,
    *,
    firstname: str,
    lastname: str,
    affiliation_hints: list[str],
    rows: int = 5,
) -> list[dict[str, Any]]:
    """Search ORCID with each institution hint and merge unique candidates."""
    seen_ids: set[str] = set()
    merged: list[dict[str, Any]] = []

    terms: list[str | None] = list(affiliation_hints[:3]) if affiliation_hints else [None]
    if affiliation_hints:
        terms.append(None)

    for affiliation in terms:
        batch = client.search(
            firstname=firstname,
            lastname=lastname,
            affiliation=affiliation,
            rows=rows,
        )
        for item in batch:
            orcid_id = str(item.get("orcid-id") or "")
            if not orcid_id or orcid_id in seen_ids:
                continue
            seen_ids.add(orcid_id)
            merged.append(item)
    return merged
