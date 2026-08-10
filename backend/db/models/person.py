from __future__ import annotations

from sqlalchemy import CheckConstraint, Index, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from db.models import enums
from db.models.base import Base, RowId, TimestampMixin


class Person(Base, TimestampMixin):
    """A researcher.

    ORCID is not stored here — it lives in `external_identifiers` so that every
    external id has one home and one uniqueness rule.
    """

    __tablename__ = "people"
    __table_args__ = (
        CheckConstraint(
            "btrim(firstname) <> ''", name="people_firstname_not_blank"
        ),
        CheckConstraint("btrim(lastname) <> ''", name="people_lastname_not_blank"),
        Index("idx_people_lastname", "lastname"),
        Index(
            "idx_people_firstname_trgm",
            "firstname",
            postgresql_using="gin",
            postgresql_ops={"firstname": "gin_trgm_ops"},
        ),
        Index(
            "idx_people_lastname_trgm",
            "lastname",
            postgresql_using="gin",
            postgresql_ops={"lastname": "gin_trgm_ops"},
        ),
    )

    id: Mapped[int] = mapped_column(RowId, primary_key=True, autoincrement=True)
    firstname: Mapped[str] = mapped_column(Text, nullable=False)
    middlename: Mapped[str | None] = mapped_column(Text)
    lastname: Mapped[str] = mapped_column(Text, nullable=False)
    biography: Mapped[str | None] = mapped_column(Text)
    homepage_url: Mapped[str | None] = mapped_column(Text)
    cv_url: Mapped[str | None] = mapped_column(Text)
    cv_snapshot_id: Mapped[int | None] = mapped_column(RowId)
    claimed_status: Mapped[str] = mapped_column(
        enums.claimed_status, nullable=False, server_default=text("'unclaimed'")
    )


class PersonAlias(Base):
    """Alternate spelling or transliteration, indexed for fuzzy search."""

    __tablename__ = "person_aliases"
    __table_args__ = (
        CheckConstraint("btrim(alias) <> ''", name="person_aliases_not_blank"),
        Index(
            "idx_person_aliases_trgm",
            "alias",
            postgresql_using="gin",
            postgresql_ops={"alias": "gin_trgm_ops"},
        ),
    )

    person_id: Mapped[int] = mapped_column(RowId, primary_key=True)
    alias: Mapped[str] = mapped_column(Text, primary_key=True)
