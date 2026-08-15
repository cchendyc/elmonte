from __future__ import annotations

"""Public-id decoding with GraphQL field errors."""

from graphql import GraphQLError

from api.id_codec import decode


def _decode_any(public_id: str) -> tuple[str, int]:
    """Decode *public_id* into *(kind, row_id)*.

    Raises :class:`GraphQLError` — surfaced as a field error in the GraphQL
    response, not a 500 — for malformed ids.
    """
    try:
        return decode(public_id)
    except ValueError as exc:
        raise GraphQLError(f"invalid id: {public_id!r}") from exc
def _decode_id(public_id: str, expected: str | None = None) -> int:
    """Decode *public_id* into an internal row id, optionally kind-checked."""
    kind, row_id = _decode_any(public_id)
    if expected is not None and kind != expected:
        raise GraphQLError(f"{public_id!r} is not a {expected} id")
    return row_id
