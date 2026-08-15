from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

from sqlalchemy import (
    CHAR,
    CheckConstraint,
    Computed,
    DateTime,
    Identity,
    Index,
    Numeric,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import DATERANGE
from sqlalchemy.orm import Mapped, mapped_column

from db.models import enums
from db.models.base import VALIDITY_EXPRESSION, Base, RowId, TimestampMixin


class Grant(Base, TimestampMixin):
    __tablename__ = "grants"
    __table_args__ = (
        CheckConstraint("amount IS NULL OR amount >= 0", name="grants_amount_non_negative"),
        CheckConstraint(
            "currency IS NULL OR currency ~ '^[A-Z]{3}$'", name="grants_currency_iso"
        ),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at",
            name="grants_date_order",
        ),
        Index(
            "uq_grants_funder_award_number",
            "funder_org_id",
            "award_number",
            unique=True,
        ),
        Index("idx_grants_funder", "funder_org_id"),
        Index("idx_grants_validity", "validity", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(RowId, Identity(), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    funder_org_id: Mapped[int] = mapped_column(RowId, nullable=False)
    award_number: Mapped[str | None] = mapped_column(Text)
    amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    currency: Mapped[str | None] = mapped_column(CHAR(3))
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validity: Mapped[Any | None] = mapped_column(
        DATERANGE, Computed(VALIDITY_EXPRESSION, persisted=True)
    )
    verification_status: Mapped[str] = mapped_column(
        enums.verification_status, nullable=False, server_default=text("'unverified'")
    )


class GrantParticipant(Base):
    __tablename__ = "grant_participants"
    __table_args__ = (
        Index("idx_grant_participants_person", "person_id"),
        Index("idx_grant_participants_org", "organization_id"),
    )

    grant_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    person_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    organization_id: Mapped[int | None] = mapped_column(RowId)
    role: Mapped[str] = mapped_column(
        enums.grant_role,
        primary_key=True,
        server_default=text("'principal_investigator'"),
    )
