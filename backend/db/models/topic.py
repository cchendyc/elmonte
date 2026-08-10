"""OpenAlex topics and per-person topic profiles."""

from __future__ import annotations

from sqlalchemy import Boolean, Index, Integer, SmallInteger, Text, text
from sqlalchemy.dialects.postgresql import REAL
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, CreatedAtMixin, RowId


class Topic(Base, CreatedAtMixin):
    """One OpenAlex topic with its subfield/field/domain lineage."""

    __tablename__ = "topics"

    openalex_topic_id: Mapped[str] = mapped_column(Text, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=False)
    subfield_name: Mapped[str | None] = mapped_column(Text)
    field_name: Mapped[str | None] = mapped_column(Text)
    domain_name: Mapped[str | None] = mapped_column(Text)
    level: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("3")
    )


class PublicationTopic(Base):
    __tablename__ = "publication_topics"
    __table_args__ = (Index("idx_publication_topics_topic", "topic_id"),)

    publication_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    topic_id: Mapped[str] = mapped_column(Text, primary_key=True)
    score: Mapped[float | None] = mapped_column(REAL)
    is_primary: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )


class PersonTopic(Base):
    __tablename__ = "person_topics"
    __table_args__ = (Index("idx_person_topics_topic", "topic_id"),)

    person_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    topic_id: Mapped[str] = mapped_column(Text, primary_key=True)
    score: Mapped[float] = mapped_column(REAL, nullable=False)
    works_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
