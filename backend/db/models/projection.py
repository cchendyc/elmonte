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
    SmallInteger,
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
        PrimaryKeyConstraint("run_id", "person_id", "view"),
        Index("idx_person_projections_2d_person", "person_id"),
    )

    run_id: Mapped[int] = mapped_column(RowId, nullable=False)
    person_id: Mapped[int] = mapped_column(RowId, nullable=False)
    view: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'topic'"))
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    cluster_id: Mapped[int | None] = mapped_column(SmallInteger)


class ProjectionCluster(Base):
    __tablename__ = "projection_clusters"
    __table_args__ = (PrimaryKeyConstraint("run_id", "view", "cluster_index"),)

    run_id: Mapped[int] = mapped_column(RowId, nullable=False)
    view: Mapped[str] = mapped_column(Text, nullable=False)
    cluster_index: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str | None] = mapped_column(Text)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cx: Mapped[float] = mapped_column(Float, nullable=False)
    cy: Mapped[float] = mapped_column(Float, nullable=False)
    color_slot: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, server_default=text("0")
    )


class ProjectionClusterEdge(Base):
    __tablename__ = "projection_cluster_edges"
    __table_args__ = (PrimaryKeyConstraint("run_id", "view", "source_cluster", "target_cluster"),)

    run_id: Mapped[int] = mapped_column(RowId, nullable=False)
    view: Mapped[str] = mapped_column(Text, nullable=False)
    source_cluster: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    target_cluster: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    collaboration_weight: Mapped[float | None] = mapped_column(Float)
    topic_weight: Mapped[float | None] = mapped_column(Float)
