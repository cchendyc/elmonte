from __future__ import annotations

from datetime import date

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    Identity,
    Index,
    Integer,
    SmallInteger,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.models import enums
from db.models.base import Base, RowId, TimestampMixin


class Publication(Base, TimestampMixin):
    __tablename__ = "publications"
    __table_args__ = (
        CheckConstraint(
            "publication_year BETWEEN 1500 AND 2200", name="publications_year_sane"
        ),
        CheckConstraint(
            "cited_by_count IS NULL OR cited_by_count >= 0",
            name="publications_cited_by_non_negative",
        ),
        Index("idx_publications_year", "publication_year"),
        Index(
            "idx_publications_venue",
            "venue_org_id",
            postgresql_where=text("venue_org_id IS NOT NULL"),
        ),
        Index(
            "idx_publications_title_trgm",
            "title",
            postgresql_using="gin",
            postgresql_ops={"title": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(RowId, Identity(), primary_key=True)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    publication_year: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    publication_date: Mapped[date | None] = mapped_column(Date)
    cited_by_count: Mapped[int | None] = mapped_column(Integer)
    venue_org_id: Mapped[int | None] = mapped_column(RowId)


class PublicationAuthor(Base):
    __tablename__ = "publication_authors"
    __table_args__ = (
        CheckConstraint(
            "author_position > 0", name="publication_authors_position_positive"
        ),
        Index(
            "uq_publication_authors_position",
            "publication_id",
            "author_position",
            unique=True,
        ),
        Index("idx_publication_authors_person", "person_id"),
    )

    publication_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    person_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    author_position: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    is_corresponding: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("false")
    )


class PublicationAuthorAffiliation(Base):
    __tablename__ = "publication_author_affiliations"
    __table_args__ = (
        Index(
            "uq_pub_author_affiliations_triple",
            "publication_id",
            "person_id",
            "organization_id",
            unique=True,
        ),
        Index("idx_pub_author_affiliations_org", "organization_id"),
    )

    id: Mapped[int] = mapped_column(RowId, Identity(), primary_key=True)
    publication_id: Mapped[int] = mapped_column(RowId, nullable=False)
    person_id: Mapped[int] = mapped_column(RowId, nullable=False)
    organization_id: Mapped[int] = mapped_column(RowId, nullable=False)
    verification_status: Mapped[str] = mapped_column(
        enums.verification_status, nullable=False, server_default=text("'unverified'")
    )


class PublicationCitation(Base):
    __tablename__ = "publication_citations"
    __table_args__ = (
        CheckConstraint(
            "citing_publication_id <> cited_publication_id",
            name="publication_citations_no_self",
        ),
        Index("idx_publication_citations_cited", "cited_publication_id"),
    )

    citing_publication_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    cited_publication_id: Mapped[int] = mapped_column(RowId, primary_key=True)
