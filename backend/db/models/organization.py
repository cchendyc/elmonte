from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Computed,
    DateTime,
    Index,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import DATERANGE, ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from db.models import enums
from db.models.base import VALIDITY_EXPRESSION, Base, RowId, TimestampMixin


class Organization(Base, TimestampMixin):
    """Every organization: university, department, lab, company, funder, publisher."""

    __tablename__ = "organizations"
    __table_args__ = (
        CheckConstraint("btrim(name) <> ''", name="organizations_name_not_blank"),
        CheckConstraint(
            "country IS NULL OR country ~ '^[A-Z]{2}$'",
            name="organizations_country_iso",
        ),
        Index("idx_organizations_kind", "kind"),
        Index(
            "idx_organizations_name_trgm",
            "name",
            postgresql_using="gin",
            postgresql_ops={"name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(RowId, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    short_name: Mapped[str | None] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(enums.org_kind, nullable=False)
    country: Mapped[str | None] = mapped_column(Text)
    homepage_url: Mapped[str | None] = mapped_column(Text)
    description: Mapped[str | None] = mapped_column(Text)
    is_context_only: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class OrgRelationship(Base, TimestampMixin):
    """Temporal parent/child edge. The only source of truth for hierarchy."""

    __tablename__ = "org_relationships"
    __table_args__ = (
        CheckConstraint(
            "child_org_id <> parent_org_id", name="org_relationships_no_self"
        ),
        CheckConstraint(
            "ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at",
            name="org_relationships_date_order",
        ),
        ExcludeConstraint(
            ("child_org_id", "="),
            ("validity", "&&"),
            using="gist",
            where=text("relationship_type = 'primary'"),
            name="org_relationships_one_primary_parent",
        ),
        Index("idx_org_relationships_parent", "parent_org_id"),
        Index("idx_org_relationships_child", "child_org_id"),
        Index("idx_org_relationships_validity", "validity", postgresql_using="gist"),
    )

    id: Mapped[int] = mapped_column(RowId, primary_key=True, autoincrement=True)
    child_org_id: Mapped[int] = mapped_column(RowId, nullable=False)
    parent_org_id: Mapped[int] = mapped_column(RowId, nullable=False)
    relationship_type: Mapped[str] = mapped_column(
        enums.org_relationship_type, nullable=False, server_default=text("'primary'")
    )
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validity: Mapped[Any | None] = mapped_column(
        DATERANGE, Computed(VALIDITY_EXPRESSION, persisted=True)
    )
    verification_status: Mapped[str] = mapped_column(
        enums.verification_status, nullable=False, server_default=text("'unverified'")
    )
