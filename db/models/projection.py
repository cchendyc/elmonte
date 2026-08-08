"""Person 2D projections for the scatter canvas.

Each `EmbeddingRun` is a full offline build. `PersonProjection2D` stores the
2D coordinates for a specific run. Exactly one run is marked `is_active` at
a time — the frontend queries the active run by default.
"""

from __future__ import annotations

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Float,
    Index,
    Integer,
    PrimaryKeyConstraint,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from db.models.base import Base, CreatedAtMixin, RowId


class EmbeddingRun(Base, CreatedAtMixin):
    __tablename__ = "embedding_runs"
    __table_args__ = (
        CheckConstraint("raw_dim > 0", name="embedding_runs_dim_positive"),
        # `embedding_runs_one_active` — a partial UNIQUE INDEX over is_active
        # WHERE is_active — lives in schema.sql / the migration. No expression
        # for it here; SQLAlchemy doesn't emit partial unique indexes anyway.
    )

    id: Mapped[int] = mapped_column(RowId, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    algorithm: Mapped[str] = mapped_column(Text, nullable=False)
    raw_dim: Mapped[int] = mapped_column(Integer, nullable=False)
    point_count: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("FALSE")
    )
    notes: Mapped[str | None] = mapped_column(Text)


class PersonProjection2D(Base):
    __tablename__ = "person_projections_2d"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", "person_id"),
        Index("idx_person_projections_2d_person", "person_id"),
    )

    run_id: Mapped[int] = mapped_column(RowId, nullable=False)
    person_id: Mapped[int] = mapped_column(RowId, nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
