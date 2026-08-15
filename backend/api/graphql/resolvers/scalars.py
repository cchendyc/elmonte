from __future__ import annotations

"""Date scalar (serializer + value/literal parsers)."""

from datetime import date
from typing import Any

from ariadne import ScalarType
from graphql import GraphQLError

date_scalar = ScalarType("Date")


@date_scalar.serializer
def _serialize_date(value: date) -> str:
    return value.isoformat()


@date_scalar.value_parser
def _parse_date_value(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise GraphQLError(f"Invalid date: {value!r}") from None


@date_scalar.literal_parser
def _parse_date_literal(ast: Any) -> date:
    value = getattr(ast, "value", None)
    if not isinstance(value, str):
        raise GraphQLError(f"Invalid date literal: {value!r}")
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise GraphQLError(f"Invalid date: {value!r}") from None


