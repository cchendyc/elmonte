"""Split people.display_name into firstname, middlename, lastname.

Revision ID: 5a41dfb4d79c
Revises: 9a20bc7cc432
Create Date: 2026-07-30
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from db.names import parse_full_name
from sqlalchemy import text

revision = "5a41dfb4d79c"
down_revision = "9a20bc7cc432"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    def _col(table: str, name: str) -> bool:
        return conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c)"
            ),
            {"t": table, "c": name},
        ).scalar()
    if _col("people", "firstname"):
        return
    op.add_column("people", sa.Column("firstname", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("middlename", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("lastname", sa.Text(), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(text("SELECT id, display_name FROM people")).all()
    for row in rows:
        parsed = parse_full_name(row.display_name)
        conn.execute(
            text(
                """
                UPDATE people
                SET firstname = :firstname,
                    middlename = :middlename,
                    lastname = :lastname
                WHERE id = :id
                """
            ),
            {
                "id": row.id,
                "firstname": parsed.firstname,
                "middlename": parsed.middlename,
                "lastname": parsed.lastname,
            },
        )

    op.drop_index("idx_people_display_name_trgm", table_name="people")
    op.drop_index("idx_people_sort_name", table_name="people")
    op.drop_constraint("people_display_name_not_blank", "people", type_="check")
    op.drop_column("people", "display_name")
    op.drop_column("people", "sort_name")

    op.alter_column("people", "firstname", nullable=False)
    op.alter_column("people", "lastname", nullable=False)

    op.create_check_constraint(
        "people_firstname_not_blank", "people", "btrim(firstname) <> ''"
    )
    op.create_check_constraint(
        "people_lastname_not_blank", "people", "btrim(lastname) <> ''"
    )
    op.create_index("idx_people_lastname", "people", ["lastname"])
    op.create_index(
        "idx_people_firstname_trgm",
        "people",
        ["firstname"],
        postgresql_using="gin",
        postgresql_ops={"firstname": "gin_trgm_ops"},
    )
    op.create_index(
        "idx_people_lastname_trgm",
        "people",
        ["lastname"],
        postgresql_using="gin",
        postgresql_ops={"lastname": "gin_trgm_ops"},
    )


def downgrade() -> None:
    conn = op.get_bind()
    def _col(table: str, name: str) -> bool:
        return conn.execute(
            sa.text(
                "SELECT EXISTS (SELECT 1 FROM information_schema.columns WHERE table_name=:t AND column_name=:c)"
            ),
            {"t": table, "c": name},
        ).scalar()
    if not _col("people", "firstname"):
        return
    op.add_column("people", sa.Column("display_name", sa.Text(), nullable=True))
    op.add_column("people", sa.Column("sort_name", sa.Text(), nullable=True))

    conn = op.get_bind()
    rows = conn.execute(
        text("SELECT id, firstname, middlename, lastname FROM people")
    ).all()
    for row in rows:
        parts = [row.firstname]
        if row.middlename:
            parts.append(row.middlename)
        parts.append(row.lastname)
        display_name = " ".join(parts)
        sort_name = (
            f"{row.lastname}, {row.firstname}"
            + (f" {row.middlename}" if row.middlename else "")
        )
        conn.execute(
            text(
                """
                UPDATE people
                SET display_name = :display_name, sort_name = :sort_name
                WHERE id = :id
                """
            ),
            {
                "id": row.id,
                "display_name": display_name,
                "sort_name": sort_name,
            },
        )

    op.drop_index("idx_people_lastname_trgm", table_name="people")
    op.drop_index("idx_people_firstname_trgm", table_name="people")
    op.drop_index("idx_people_lastname", table_name="people")
    op.drop_constraint("people_lastname_not_blank", "people", type_="check")
    op.drop_constraint("people_firstname_not_blank", "people", type_="check")
    op.drop_column("people", "firstname")
    op.drop_column("people", "middlename")
    op.drop_column("people", "lastname")

    op.alter_column("people", "display_name", nullable=False)
    op.create_check_constraint(
        "people_display_name_not_blank", "people", "btrim(display_name) <> ''"
    )
    op.create_index(
        "idx_people_display_name_trgm",
        "people",
        ["display_name"],
        postgresql_using="gin",
        postgresql_ops={"display_name": "gin_trgm_ops"},
    )
    op.create_index("idx_people_sort_name", "people", ["sort_name"])
