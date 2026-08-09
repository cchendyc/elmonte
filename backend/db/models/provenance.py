from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.models import enums
from db.models.base import Base, CreatedAtMixin, RowId

_EVIDENCE_SUBJECTS = (
    "affiliation_id",
    "person_relationship_id",
    "org_relationship_id",
    "person_award_id",
    "grant_id",
    "pub_author_affiliation_id",
    "person_id",
    "organization_id",
)

_IDENTIFIER_SUBJECTS = (
    "person_id",
    "organization_id",
    "publication_id",
    "concept_id",
    "grant_id",
)


def _exactly_one(columns: tuple[str, ...]) -> str:
    return f"num_nonnulls({', '.join(columns)}) = 1"


class SourceSnapshot(Base):
    __tablename__ = "source_snapshots"
    __table_args__ = (
        Index("uq_source_snapshots_url_hash", "source_url", "content_hash", unique=True),
        Index("idx_source_snapshots_url", "source_url"),
    )

    id: Mapped[int] = mapped_column(RowId, primary_key=True, autoincrement=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_kind: Mapped[str] = mapped_column(enums.source_kind, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    content_hash: Mapped[str] = mapped_column(Text, nullable=False)
    local_path: Mapped[str | None] = mapped_column(Text)
    http_status: Mapped[int | None] = mapped_column(Integer)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Evidence(Base, CreatedAtMixin):
    __tablename__ = "evidence"
    __table_args__ = (
        CheckConstraint(
            _exactly_one(_EVIDENCE_SUBJECTS), name="evidence_exactly_one_subject"
        ),
        *(
            Index(
                f"idx_evidence_{column.removesuffix('_id')}",
                column,
                postgresql_where=text(f"{column} IS NOT NULL"),
            )
            for column in _EVIDENCE_SUBJECTS
        ),
        Index("idx_evidence_snapshot", "snapshot_id"),
    )

    id: Mapped[int] = mapped_column(RowId, primary_key=True, autoincrement=True)
    label: Mapped[str | None] = mapped_column(Text)
    snapshot_id: Mapped[int] = mapped_column(RowId, nullable=False)

    affiliation_id: Mapped[int | None] = mapped_column(RowId)
    person_relationship_id: Mapped[int | None] = mapped_column(RowId)
    org_relationship_id: Mapped[int | None] = mapped_column(RowId)
    person_award_id: Mapped[int | None] = mapped_column(RowId)
    grant_id: Mapped[int | None] = mapped_column(RowId)
    pub_author_affiliation_id: Mapped[int | None] = mapped_column(RowId)
    person_id: Mapped[int | None] = mapped_column(RowId)
    organization_id: Mapped[int | None] = mapped_column(RowId)


class ExternalIdentifier(Base, CreatedAtMixin):
    __tablename__ = "external_identifiers"
    __table_args__ = (
        Index(
            "uq_external_identifiers_provider_id",
            "provider",
            "external_id",
            unique=True,
        ),
        Index(
            "idx_external_identifiers_snapshot",
            "snapshot_id",
            postgresql_where=text("snapshot_id IS NOT NULL"),
        ),
        CheckConstraint(
            _exactly_one(_IDENTIFIER_SUBJECTS),
            name="external_identifiers_exactly_one_subject",
        ),
        *(
            Index(
                f"one_provider_id_per_{column.removesuffix('_id')}",
                column,
                "provider",
                unique=True,
                postgresql_where=text(f"{column} IS NOT NULL"),
            )
            for column in _IDENTIFIER_SUBJECTS
        ),
    )

    id: Mapped[int] = mapped_column(RowId, primary_key=True, autoincrement=True)
    provider: Mapped[str] = mapped_column(enums.identifier_provider, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_id: Mapped[int | None] = mapped_column(RowId)
    verified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    person_id: Mapped[int | None] = mapped_column(RowId)
    organization_id: Mapped[int | None] = mapped_column(RowId)
    publication_id: Mapped[int | None] = mapped_column(RowId)
    concept_id: Mapped[int | None] = mapped_column(RowId)
    grant_id: Mapped[int | None] = mapped_column(RowId)
