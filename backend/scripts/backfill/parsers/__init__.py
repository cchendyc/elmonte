"""Per-host HTML parsers for profile pages.

Each parser exposes:
    parse(url: str, html: str) -> ProfileExtraction

Where `ProfileExtraction` is the structured shape defined in
`scripts.backfill.common`. Parsers should be pure: no I/O, no DB.

Selection happens in `scripts.backfill.common.parser_for(url)` by hostname.
Add a new host by wiring it into the registry there.
"""

from __future__ import annotations
