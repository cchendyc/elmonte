from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Computed,
    DateTime,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import DATERANGE
from sqlalchemy.orm import Mapped, mapped_column

from db.models import enums
from db.models.base import VALIDITY_EXPRESSION, Base, RowId, TimestampMixin


class PersonRelationship(Base, TimestampMixin):
    __tablename__ = "person_relationships"
    __table_args__ = (
        CheckConstraint(
            "from_person_id <> to_person_id", name="person_relationships_no_self"
        ),
        CheckConstraint(
            "type <> 'collaborated_with' OR from_person_id < to_person_id",
            name="person_relationships_symmetric_canonical",
        ),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at",
            name="person_relationships_date_order",
        ),
        Index(
            "uq_person_relationships_edge",
            "type",
            "from_person_id",
            "to_person_id",
            unique=True,
        ),
        Index("idx_person_relationships_from", "from_person_id", "type"),
        Index("idx_person_relationships_to", "to_person_id", "type"),
    )

    id: Mapped[int] = mapped_column(RowId, primary_key=True, autoincrement=True)
    type: Mapped[str] = mapped_column(enums.person_relationship_type, nullable=False)
    from_person_id: Mapped[int] = mapped_column(RowId, nullable=False)
    to_person_id: Mapped[int] = mapped_column(RowId, nullable=False)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validity: Mapped[Any | None] = mapped_column(
        DATERANGE, Computed(VALIDITY_EXPRESSION, persisted=True)
    )
    verification_status: Mapped[str] = mapped_column(
        enums.verification_status, nullable=False, server_default=text("'unverified'")
    )
