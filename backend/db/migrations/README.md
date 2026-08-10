# Migrations

## Creating a new migration

```bash
.venv/bin/alembic revision -m "short description"
```

Alembic generates a filename like:

```
db/migrations/versions/07966a575e9a_short_description.py
```

The 12‑character prefix is a random hex slug produced by
`alembic.util.rev_id()`. It IS the `Revision ID` — that same string appears
inside the file as `revision = "07966a575e9a"` and as the parent's
`down_revision` on the next migration.

### Do not hand‑number migrations

Sequential names like `0001_x.py`, `0002_y.py` look tidy but are actively
harmful:

* If two branches each add a new migration, both files use the next number
  (say `0005`), which triggers a duplicate `revision =` and an Alembic
  "multiple heads" state that has to be manually rebased away.
* The number tells you nothing about which parent the migration builds on —
  the actual chain is expressed by `down_revision`, not by lexical order.

Random slugs are unique on generation, so merges never collide. The
`alembic.ini` `file_template` and this note both enforce this.

## Chain integrity

Verify the chain is a single line:

```bash
.venv/bin/alembic heads      # should return exactly one head
.venv/bin/alembic history    # should show <base> -> ... -> (head)
```

To confirm every step is reversible, cycle through it:

```bash
.venv/bin/alembic downgrade base
.venv/bin/alembic upgrade head
```

## Data-modifying migrations

`op.get_bind()` gives a live connection. Use it for bulk backfills — see
`5a41dfb4d79c_people_name_parts.py` for a worked example that splits a
column while preserving row values.

## Materialized view / DDL migrations

Materialized views and other non‑table DDL are declared as raw SQL strings
and driven with `op.execute(...)` — SQLAlchemy's declarative helpers don't
express them. See `f835c16a408e_hop_materialized_views.py`.
