"""Prefixed public ids <-> internal bigint ids.

Internal ids are ``BIGINT`` per db/models.  The public API surfaces them as
prefixed strings (``p:...``, ``o:...``) so the frontend can tell a person id
from an org id at a glance and route the correct expand branch without a
lookup.

**ID enumeration mitigation (privacy):**  The numeric suffix is obfuscated
with a reversible, keyed XOR + base36 transform to discourage casual
enumeration / systematic scraping.  This is **NOT** cryptographic security —
IDs must remain guessable for unauthenticated link-sharing to work — it is
purely obscurity.  The salt is read from ``ELMONTE_ID_SALT`` (a dev default
with a warning is used when unset).

**Format disjointness:** legacy ids are plain decimal strings (``p:123``).
Encoded tokens therefore never use an all-decimal suffix; if XOR + base36
would produce one, the token is prefixed with ``_`` (which is not part of
the legacy or base-36 alphabets), so the two formats can never be confused.
"""

from __future__ import annotations

import hashlib
import logging
import os
from typing import Literal

logger = logging.getLogger(__name__)

NodeKind = Literal["person", "org"]

_PREFIXES: dict[NodeKind, str] = {"person": "p", "org": "o"}
_INV: dict[str, NodeKind] = {v: k for k, v in _PREFIXES.items()}

# ---------------------------------------------------------------------------
# Obfuscation salt
# ---------------------------------------------------------------------------

_ENV = os.environ.get("ELMONTE_ENV", "development")
_configured_salt = os.environ.get("ELMONTE_ID_SALT")

if _configured_salt:
    ID_SALT = _configured_salt
elif _ENV == "production":
    # Refusing to start is the point: with the dev salt every public id is
    # trivially reversible, which defeats the enumeration mitigation.
    raise RuntimeError(
        "ELMONTE_ID_SALT must be set when ELMONTE_ENV=production — "
        "public ids would otherwise be trivially reversible"
    )
else:
    ID_SALT = "elmonte-dev-salt"
    logger.warning(
        "ELMONTE_ID_SALT is not set — using the default dev salt.  "
        "Public ids are only lightly obfuscated."
    )

_ALPHABET = "0123456789abcdefghijklmnopqrstuvwxyz"

# PostgreSQL BIGINT range.  Decode rejects values outside it instead of
# letting a crafted id surface as a bind error deep inside a resolver.
_BIGINT_MIN = 0
_BIGINT_MAX = (1 << 63) - 1

# Newly-encoded tokens are guaranteed never to be purely decimal, so the
# legacy `p:123` interpretation and the obfuscated format can never collide.
# A `_` prefix marks a base-36 token that happened to be all digits.
_DECIMAL_TOKEN_PREFIX = "_"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _derive_mask(salt: str) -> int:
    """Derive a stable 64-bit mask from a salt string."""
    mask_bytes = hashlib.sha256(salt.encode()).digest()[:8]
    return int.from_bytes(mask_bytes, "big")


# Module-level mask from the configured salt (or dev default).
_XOR_MASK: int = _derive_mask(ID_SALT)


def _base36_encode(n: int) -> str:
    """Encode a non-negative integer as a base-36 string (digits + lowercase)."""
    if n == 0:
        return "0"
    chars = []
    while n > 0:
        n, r = divmod(n, 36)
        chars.append(_ALPHABET[r])
    return "".join(reversed(chars))


def _base36_decode(s: str) -> int:
    """Decode a base-36 string back to an integer.

    Accepts uppercase input (URLs are case-preserving, and some clients
    uppercase pasted ids).  Raises :class:`ValueError` for an empty string
    or invalid characters.
    """
    if not s:
        raise ValueError("empty base36 string")
    n = 0
    for ch in s.lower():
        n = n * 36 + _ALPHABET.index(ch)
    return n


def _validate_row_id(row_id: int) -> None:
    """IDs must be valid PostgreSQL BIGINT row ids."""
    if isinstance(row_id, bool) or not isinstance(row_id, int):
        raise ValueError(  # noqa: TRY004
            f"row id must be an integer, got {type(row_id).__name__}"
        )
    if row_id < _BIGINT_MIN or row_id > _BIGINT_MAX:
        raise ValueError(
            f"row id {row_id} is outside the BIGINT range "
            f"[{_BIGINT_MIN}, {_BIGINT_MAX}]"
        )


def _obfuscate(row_id: int, mask: int | None = None) -> str:
    """Deterministic, reversible obfuscation of a non-negative integer.

    XOR with a 64-bit keyed mask then encode as base-36.  Injectivity:
    XOR is a bijection on a fixed bit-width, and base36 is a bijection
    from non-negative integers to their base-36 representation.

    If *mask* is ``None`` the module-level :data:`_XOR_MASK` (derived from
    ``ELMONTE_ID_SALT``) is used.
    """
    _validate_row_id(row_id)
    if mask is None:
        mask = _XOR_MASK
    obfuscated = row_id ^ mask
    token = _base36_encode(obfuscated)
    if token.isdigit():
        # ``p:100`` is reserved for the legacy numeric-id path.  Marking an
        # all-digit base-36 token makes the two formats disjoint.
        token = _DECIMAL_TOKEN_PREFIX + token
    return token


def _deobfuscate(token: str, mask: int | None = None) -> int:
    """Reverse :func:`_obfuscate` — base36-decode then XOR back.

    If *mask* is ``None`` the module-level :data:`_XOR_MASK` is used.
    """
    if mask is None:
        mask = _XOR_MASK
    token = token.removeprefix(_DECIMAL_TOKEN_PREFIX)
    n = _base36_decode(token)
    row_id = n ^ mask
    _validate_row_id(row_id)
    return row_id


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def encode(kind: NodeKind, row_id: int, salt: str | None = None) -> str:
    """Encode *row_id* into an obfuscated public identifier.

    Example: ``encode("person", 42)`` → ``"p:1abc…"``.

    If *salt* is provided it overrides the ``ELMONTE_ID_SALT`` environment
    variable for this call, which is useful for testing.
    """
    if kind not in _PREFIXES:
        raise ValueError(f"unknown id kind: {kind!r}")
    _validate_row_id(row_id)
    mask = _derive_mask(salt) if salt is not None else _XOR_MASK
    return f"{_PREFIXES[kind]}:{_obfuscate(row_id, mask)}"


def decode(public_id: str, salt: str | None = None) -> tuple[NodeKind, int]:
    """Decode a public identifier back to *(kind, row_id)*.

    **Backward compatibility:**  If the suffix is composed entirely of
    decimal digits it is treated as a legacy (pre-obfuscation) numeric id
    and returned directly — no deobfuscation is attempted.  All suffices
    produced by the current :func:`encode` are base-36 and are practically
    never purely decimal for ids > 9; a collision is astronomically
    unlikely.

    If *salt* is provided it overrides the ``ELMONTE_ID_SALT`` environment
    variable for this call, which is useful for testing.

    Raises :class:`ValueError` for malformed or unknown-format ids.
    """
    if not isinstance(public_id, str) or ":" not in public_id:
        raise ValueError(f"malformed id: {public_id!r}")
    prefix, tail = public_id.split(":", 1)
    if prefix not in _INV:
        raise ValueError(f"unknown id prefix: {prefix!r}")
    if not tail:
        raise ValueError(f"malformed id: {public_id!r}")

    # Legacy numeric ids — accept unchanged for backward compatibility.
    if tail.isdigit():
        row_id = int(tail)
        _validate_row_id(row_id)
        return _INV[prefix], row_id

    # Obfuscated (current) format.
    mask = _derive_mask(salt) if salt is not None else _XOR_MASK
    try:
        row_id = _deobfuscate(tail, mask)
    except (ValueError, KeyError) as exc:
        raise ValueError(f"malformed id: {public_id!r}") from exc

    return _INV[prefix], row_id
