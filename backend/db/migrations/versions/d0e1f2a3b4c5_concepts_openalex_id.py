"""concepts: add openalex_id so the bulk taxonomy backfill can link parents.

Revision ID: d0e1f2a3b4c5
Revises: fbb6b2990cac
"""

from __future__ import annotations

from alembic import op

revision = "d0e1f2a3b4c5"
down_revision = "d4d636fc3df0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE concepts ADD COLUMN openalex_id TEXT")
    op.execute("ALTER TABLE concepts ADD COLUMN parent_openalex_id TEXT")
    # Non-partial unique index: ON CONFLICT (openalex_id) inference requires a
    # full constraint (partial indexes can't be conflict arbiters).  NULLs
    # stay distinct in a plain unique index, so legacy rows without an id are
    # unaffected.
    op.execute(
        "CREATE UNIQUE INDEX uq_concepts_openalex_id ON concepts (openalex_id)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS uq_concepts_openalex_id")
    op.execute("ALTER TABLE concepts DROP COLUMN IF EXISTS openalex_id")
    op.execute("ALTER TABLE concepts DROP COLUMN IF EXISTS parent_openalex_id")
