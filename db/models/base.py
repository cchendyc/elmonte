from __future__ import annotations

from datetime import datetime

from sqlalchemy import BigInteger, DateTime, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

VALIDITY_EXPRESSION = (
    "daterange((starts_at AT TIME ZONE 'UTC')::date, (ends_at AT TIME ZONE 'UTC')::date, '[)')"
)
"""Half-open validity range generated from starts_at/ends_at timestamps.

A NULL bound means unbounded, which `&&` and `@>` handle natively. Date
components are cast from `starts_at` / `ends_at` timestamps for GiST indexing.
"""

# Internal row ids and reference columns. No FK constraints — integrity in app layer.
RowId = BigInteger


class Base(DeclarativeBase):
    pass


class CreatedAtMixin:
    """Row creation timestamp."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TimestampMixin(CreatedAtMixin):
    """Creation and modification timestamps.

    `updated_at` is maintained by the `set_updated_at()` trigger declared in
    schema.sql; SQLAlchemy does not manage it.
    """

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
