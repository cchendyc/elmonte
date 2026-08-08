"""Prefixed public ids <-> internal bigint ids.

Internal ids are `BIGINT` per db/models. The public API surfaces them as
prefixed strings (`p:123`, `o:45`) so the frontend can tell a person id from
an org id at a glance and route the correct expand branch without a lookup.
"""

from __future__ import annotations

from typing import Literal


NodeKind = Literal["person", "org"]

_PREFIXES: dict[NodeKind, str] = {"person": "p", "org": "o"}
_INV: dict[str, NodeKind] = {v: k for k, v in _PREFIXES.items()}


def encode(kind: NodeKind, row_id: int) -> str:
    return f"{_PREFIXES[kind]}:{row_id}"


def decode(public_id: str) -> tuple[NodeKind, int]:
    if ":" not in public_id:
        raise ValueError(f"malformed id: {public_id!r}")
    prefix, tail = public_id.split(":", 1)
    if prefix not in _INV:
        raise ValueError(f"unknown id prefix: {prefix!r}")
    try:
        row_id = int(tail)
    except ValueError as exc:
        raise ValueError(f"malformed id: {public_id!r}") from exc
    return _INV[prefix], row_id
