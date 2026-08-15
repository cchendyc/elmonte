"""Initial schema from db/schema.sql.

Revision ID: 9a20bc7cc432
Revises:
Create Date: 2026-07-30
"""

from __future__ import annotations

from db.migrations.sql import apply_schema, teardown_schema

revision = "9a20bc7cc432"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    from alembic import op

    apply_schema(op.get_bind())


def downgrade() -> None:
    from alembic import op

    teardown_schema(op.get_bind())
