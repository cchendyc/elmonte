from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Identity,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import DATERANGE, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.models import enums
from db.models.base import VALIDITY_EXPRESSION, Base, RowId, TimestampMixin


class PersonAffiliation(Base, TimestampMixin):
    """A person's tie to one or more organizations over a time range."""

    __tablename__ = "person_affiliations"
    __table_args__ = (
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at",
            name="person_affiliations_date_order",
        ),
        ExcludeConstraint(
            ("person_id", "="),
            ("validity", "&&"),
            using="gist",
            where=text("is_primary"),
            name="person_affiliations_one_primary",
        ),
        Index("idx_person_affiliations_person", "person_id"),
        Index("idx_person_affiliations_validity", "validity", postgresql_using="gist"),
        Index(
            "idx_person_affiliations_current",
            "person_id",
            postgresql_where=text("ends_at IS NULL"),
        ),
        Index(
            "idx_person_affiliations_rank",
            "position_rank",
            postgresql_where=text("position_rank IS NOT NULL"),
        ),
    )

    id: Mapped[int] = mapped_column(RowId, Identity(), primary_key=True)
    person_id: Mapped[int] = mapped_column(RowId, nullable=False)
    title: Mapped[str | None] = mapped_column(Text)
    affiliation_kind: Mapped[str] = mapped_column(
        enums.affiliation_kind, nullable=False
    )
    position_rank: Mapped[str | None] = mapped_column(enums.position_rank)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validity: Mapped[Any | None] = mapped_column(
        DATERANGE, Computed(VALIDITY_EXPRESSION, persisted=True)
    )
    verification_status: Mapped[str] = mapped_column(
        enums.verification_status, nullable=False, server_default=text("'unverified'")
    )


class AffiliationOrgAssignment(Base):
    """Which organizations an affiliation attaches to."""

    __tablename__ = "affiliation_org_assignments"
    __table_args__ = (
        Index(
            "one_chart_anchor_per_affiliation",
            "affiliation_id",
            unique=True,
            postgresql_where=text("assignment_type = 'chart_anchor'"),
        ),
        Index("idx_affiliation_assignments_org", "organization_id"),
    )

    affiliation_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    organization_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    assignment_type: Mapped[str] = mapped_column(
        enums.assignment_type, nullable=False, server_default=text("'secondary'")
    )
