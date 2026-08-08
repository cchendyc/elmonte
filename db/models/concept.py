from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import REAL
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, CreatedAtMixin, RowId


class Concept(Base, CreatedAtMixin):
    """A research field or topic."""

    __tablename__ = "concepts"
    __table_args__ = (
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id", name="concepts_no_self_parent"
        ),
        CheckConstraint("level >= 0", name="concepts_level_non_negative"),
        Index("uq_concepts_display_name", "display_name", unique=True),
        Index("idx_concepts_parent", "parent_id"),
        Index(
            "idx_concepts_name_trgm",
            "display_name",
            postgresql_using="gin",
            postgresql_ops={"display_name": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(RowId, primary_key=True, autoincrement=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    parent_id: Mapped[int | None] = mapped_column(RowId)
    level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )


class PersonConcept(Base):
    __tablename__ = "person_concepts"
    __table_args__ = (
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="person_concepts_score_range",
        ),
        CheckConstraint(
            "rank IS NULL OR rank > 0", name="person_concepts_rank_positive"
        ),
        Index("idx_person_concepts_concept", "concept_id"),
    )

    person_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    concept_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    score: Mapped[float | None] = mapped_column(REAL)
    rank: Mapped[int | None] = mapped_column(SmallInteger)


class PublicationConcept(Base):
    __tablename__ = "publication_concepts"
    __table_args__ = (
        CheckConstraint(
            "score IS NULL OR (score >= 0 AND score <= 1)",
            name="publication_concepts_score_range",
        ),
        Index("idx_publication_concepts_concept", "concept_id"),
    )

    publication_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    concept_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    score: Mapped[float | None] = mapped_column(REAL)
