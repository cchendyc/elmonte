from __future__ import annotations

from datetime import datetime

from sqlalchemy import DateTime, Identity, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db.models import enums
from db.models.base import Base, CreatedAtMixin, RowId


class Award(Base, CreatedAtMixin):
    __tablename__ = "awards"
    __table_args__ = (
        Index("uq_awards_name", "name", unique=True),
        Index("idx_awards_awarding_org", "awarding_org_id"),
    )

    id: Mapped[int] = mapped_column(RowId, Identity(), primary_key=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    awarding_org_id: Mapped[int | None] = mapped_column(RowId)


class PersonAward(Base, CreatedAtMixin):
    __tablename__ = "person_awards"
    __table_args__ = (
        Index(
            "uq_person_awards_person_award_date",
            "person_id",
            "award_id",
            "awarded_at",
            unique=True,
        ),
        Index("idx_person_awards_person", "person_id"),
        Index("idx_person_awards_award", "award_id"),
    )

    id: Mapped[int] = mapped_column(RowId, Identity(), primary_key=True)
    person_id: Mapped[int] = mapped_column(RowId, nullable=False)
    award_id: Mapped[int] = mapped_column(RowId, nullable=False)
    awarded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    verification_status: Mapped[str] = mapped_column(
        enums.verification_status, nullable=False, server_default=text("'unverified'")
    )
