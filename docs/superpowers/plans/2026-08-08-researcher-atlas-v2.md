# El Monte Researcher Atlas v2 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Rebuild the professor scatter map as a two-view (topic / network), two-level (cluster + member) atlas with weighted inter-cluster edges, replacing the degenerate all-pairs `D = 1 - S` layout.

**Architecture:** Standard bibliometric-mapping pipeline. (1) Signals: coauthor/advisor edges normalized by **association strength**, OpenAlex **topics** backfilled into `person_topics`. (2) Clusters: disparity-filter backbone → **Leiden** communities (network view); dominant OpenAlex field coarse-clustered + Ward refinement (topic view). (3) Two-level layout: cluster-level linlog / MDS, member-level local spring / local MDS inside each cluster footprint. (4) Both inter-cluster edge semantics (collaboration weight, topic weight) computed per view and switchable in the UI. All heavy computation happens offline into Postgres; the frontend only renders.

**Tech Stack:** Python 3 (numpy, scipy, python-igraph, SQLAlchemy, alembic, FastAPI/Ariadne), TypeScript (React 19, Apollo Client, SVG), Postgres.

## Global Constraints

- `view` values are exactly `"topic"` and `"network"` (GraphQL enum-lite: validated in resolver, default `"topic"`).
- Deterministic: every RNG gets a fixed seed (`42` base; per-cluster seeds `seed + cluster_index`). Runs must be reproducible byte-for-byte.
- Distance semantics: 2D distance must stay proportional to target distance (∝ 1/w^p). Never globally rescale or log/compress coordinates for readability; overlap removal is local-only (push apart only pairs below min separation). Cross-group relations are carried by weighted cluster edges, not by pulling nodes into impossible positions.
- No JSONB anywhere (project rule). New tables are plain relational.
- Evidence-first data rule: backfill only writes facts tied to a snapshot/external id (mirror `scripts/backfill/concepts.py`).
- `embedding_runs` keeps its partial-unique-index constraint (`embedding_runs_one_active`): exactly **one** active run; both views live under that one run via the `view` column on `person_projections_2d`.
- Python dependency additions: `python-igraph>=0.10.5` (Leiden is in the C core, no `leidenalg` needed) and `pytest>=8.0`. No torch, no networkx.
- Repository is **not yet a git repo** — Task 1 runs `git init`; every later task ends with a commit.
- No CI; DB-touching tasks verify with explicit commands against the local Postgres (`.env` `DATABASE_URL`, `api.deps._SessionLocal`).
- **No `.venv` in this repo.** Replace every `.venv/bin/python` in the task steps with `python3` (system anaconda at `/opt/anaconda3/bin/python3`, Python 3.13; `python-igraph` and `pytest` already installed there by Task 1).
- **Fresh-install migration safety:** the migration chain predates the current `schema.sql`, which is a deterministic rebuild already containing the name split, homepage_url, cv_url, mat views, and projection tables. Every historical migration on the path (5a41, f835, 07966, c994, b8e4, c3a7, d4f2) therefore carries a skip-if-end-state-present guard so `alembic upgrade head` succeeds on a fresh DB (Task 2 fix round). Local DB sequence: fresh database → `alembic upgrade head` → seed data-only (never re-run `\i schema.sql` after migrations, or the stamped `view` column is wiped).
- Do not delete existing behavior: `personCoauthorTies`, `expand`, `search`, institution color mode, focus mode must keep working.

## Design Principles (user-confirmed 2026-08-09)

- **Three layers of truth.** (1) *Data truth*: `w_ij` coauthor/citation/topic weights and the author↔paper hypergraph never change for rendering. (2) *Semantic layout truth*: landscape coordinates are canonical and STABLE — search/hover/focus never re-run layout; focus only changes opacity/labels/edges/emphasis. (3) *Render truth*: overlap removal, label decluttering, zoom are display-only displacements; the render layer may lie for readability but must never pollute the semantic layer (no autobalance that rescales semantic distance; log/compress forbidden).
- **Landscape grammar:** screen distance = global structural similarity; edge thickness = direct collaboration strength. Direct weights are expressed with edges/widths/badges/tooltips, never by asking the user to read pixel distances.
- **Coauthor ≠ cluster.** A coauthor list is an ego neighborhood, not a community; only alter-alter connectivity makes a community. Never force coauthors into a blob.
- **Perspective is a separate geometry (Task 13):** r_i = asymmetric importance of alter i to the selected person (`s_{P→A} = c_PA / Σ_j c_Pj` — P with 20/30 papers vs A with 20/300 gives s_{P→A} ≫ s_{A→P}`); θ_i = alter-alter community structure. Entering a perspective changes coordinate semantics, not just highlight.
- **Papers are hyperedges:** layout weights use fractional counting (`1/(k−1)` per paper, k = author count) so a 4-author paper doesn't inflate six independent pairwise relationships; display paperCount stays integer.

## File Structure

| File | Responsibility |
|---|---|
| `tests/test_atlas_core.py` | Pure-function tests: normalization, backbone, clustering, layouts |
| `tests/test_metrics.py` | Pure-function tests: purity, separation, fidelity, overlap, gold set |
| `scripts/embed/atlas_core.py` | Pure algorithms (numpy/scipy/igraph only — no DB imports) |
| `scripts/embed/metrics.py` | Pure metric functions + gold-set distance check |
| `scripts/embed/build_atlas.py` | DB loaders, view orchestration, CLI, run writer |
| `scripts/backfill/topics.py` | OpenAlex topics backfill → `publication_topics` + `person_topics` |
| `db/models/topic.py` | `Topic`, `PublicationTopic`, `PersonTopic` models |
| `db/models/projection.py` | Add `view`, `cluster_id` columns; `ProjectionCluster`, `ProjectionClusterEdge` models |
| `db/migrations/versions/<rev>_atlas_v2.py` | New tables + columns |
| `api/graphql/schema.graphql` | `projection(view:)`, `ProjectionCluster`, `ProjectionClusterEdge`, point fields |
| `api/graphql/resolvers.py` | `resolve_projection` gains `view`, returns clusters/edges |
| `src/api/projection.ts` | Query with `$view`, new types |
| `src/lib/scatterLayout.ts` | Pure TS: convex hull, edge width/path helpers |
| `src/lib/scatterLayout.test.ts` | vitest tests for the above |
| `src/lib/scatterColor.ts` | "similarity" mode → "cluster" mode via `colorSlot` |
| `src/components/PeopleScatter.tsx` | View toggle, edge toggle, cluster hulls/labels, weighted edges, cluster zoom |

---

### Task 1: Test infrastructure + git init

**Files:**
- Modify: `requirements.txt`
- Create: `tests/test_atlas_core.py` (first trivial test)
- Create: `pytest.ini`

**Interfaces:**
- Consumes: nothing.
- Produces: `pytest` runnable as `.venv/bin/python -m pytest tests -q`; repo becomes a git repo.

- [ ] **Step 1: Add pytest dependency**

Append to `requirements.txt`:

```text
python-igraph>=0.10.5
pytest>=8.0
```

- [ ] **Step 2: Create `pytest.ini`**

```ini
[pytest]
testpaths = tests
pythonpath = .
```

(`pythonpath = .` makes `import scripts.embed.atlas_core` work from tests.)

- [ ] **Step 3: Write a smoke test**

Create `tests/test_atlas_core.py` (verifies the new runtime deps import):

```python
def test_runtime_deps_import():
    import igraph  # noqa: F401
    import numpy as np  # noqa: F401
    import scipy  # noqa: F401

    assert igraph is not None
```

- [ ] **Step 4: Run it**

Run: `.venv/bin/python -m pytest tests -q`
Expected: 1 passed.

- [ ] **Step 5: git init + commit**

```bash
git init
git add requirements.txt pytest.ini tests/
git commit -m "chore: add pytest test harness"
```

---

### Task 2: Schema migration + models

**Files:**
- Create: `db/migrations/versions/<next>_atlas_v2.py` (compute `<next>` with `alembic heads`)
- Create: `db/models/topic.py`
- Modify: `db/models/projection.py`
- Test: `tests/test_atlas_core.py` stays untouched; verification via alembic + smoke query.

**Interfaces:**
- Consumes: existing `db/models/base.py` (`Base`, `CreatedAtMixin`, `RowId`).
- Produces tables consumed by Task 3 (`publication_topics`, `person_topics`), Task 8 (`projection_clusters`, `projection_cluster_edges`, `view`/`cluster_id` on `person_projections_2d`), Task 9 (same).

- [ ] **Step 1: Create the migration**

```bash
cd ~/Downloads/elmonte-main
.venv/bin/alembic revision -m "atlas v2 projection tables"   # note the generated revision id
```

Then replace the generated file body with:

```python
"""Atlas v2: topics, person_topics, projection views and cluster tables.

Revision ID: <generated>
Revises: <head from alembic heads>
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "<generated>"
down_revision = "<head>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE topics (
          openalex_topic_id TEXT PRIMARY KEY,
          display_name      TEXT NOT NULL,
          subfield_name     TEXT,
          field_name        TEXT,
          domain_name       TEXT,
          level             SMALLINT NOT NULL DEFAULT 3,
          created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE publication_topics (
          publication_id BIGINT NOT NULL,
          topic_id       TEXT NOT NULL,
          score          REAL,
          is_primary     BOOLEAN NOT NULL DEFAULT FALSE,
          PRIMARY KEY (publication_id, topic_id)
        )
        """
    )
    op.execute(
        "CREATE INDEX idx_publication_topics_topic ON publication_topics (topic_id)"
    )
    op.execute(
        """
        CREATE TABLE person_topics (
          person_id    BIGINT NOT NULL,
          topic_id     TEXT NOT NULL,
          score        REAL NOT NULL,
          works_count  INTEGER NOT NULL DEFAULT 0,
          PRIMARY KEY (person_id, topic_id)
        )
        """
    )
    op.execute("CREATE INDEX idx_person_topics_topic ON person_topics (topic_id)")
    op.add_column(
        "person_projections_2d",
        sa.Column("view", sa.Text, nullable=False, server_default="topic"),
    )
    op.add_column(
        "person_projections_2d",
        sa.Column("cluster_id", sa.SmallInteger, nullable=True),
    )
    # One row per (run, person, view) — the old (run_id, person_id) PK would
    # be violated by the second view's rows.
    op.execute("ALTER TABLE person_projections_2d DROP CONSTRAINT person_projections_2d_pkey")
    op.execute("ALTER TABLE person_projections_2d ADD PRIMARY KEY (run_id, person_id, view)")
    op.execute(
        """
        CREATE TABLE projection_clusters (
          run_id        BIGINT NOT NULL,
          view          TEXT NOT NULL,
          cluster_index SMALLINT NOT NULL,
          label         TEXT NOT NULL,
          field_name    TEXT,
          member_count  INTEGER NOT NULL,
          cx            DOUBLE PRECISION NOT NULL,
          cy            DOUBLE PRECISION NOT NULL,
          color_slot    SMALLINT NOT NULL DEFAULT 0,
          PRIMARY KEY (run_id, view, cluster_index)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE projection_cluster_edges (
          run_id               BIGINT NOT NULL,
          view                 TEXT NOT NULL,
          source_cluster       SMALLINT NOT NULL,
          target_cluster       SMALLINT NOT NULL,
          collaboration_weight DOUBLE PRECISION,
          topic_weight         DOUBLE PRECISION,
          PRIMARY KEY (run_id, view, source_cluster, target_cluster)
        )
        """
    )


def downgrade() -> None:
    op.drop_table("projection_cluster_edges")
    op.drop_table("projection_clusters")
    op.drop_column("person_projections_2d", "cluster_id")
    op.execute("ALTER TABLE person_projections_2d DROP CONSTRAINT person_projections_2d_pkey")
    op.execute("ALTER TABLE person_projections_2d ADD PRIMARY KEY (run_id, person_id)")
    op.drop_column("person_projections_2d", "view")
    op.drop_table("person_topics")
    op.drop_table("publication_topics")
    op.drop_table("topics")
```

- [ ] **Step 2: Create `db/models/topic.py`**

```python
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
```

- [ ] **Step 3: Extend `db/models/projection.py`**

Add `view` and `cluster_id` to `PersonProjection2D`, and two new models (append to the same file):

```python
class PersonProjection2D(Base):
    __tablename__ = "person_projections_2d"
    __table_args__ = (
        PrimaryKeyConstraint("run_id", "person_id", "view"),
        Index("idx_person_projections_2d_person", "person_id"),
    )

    run_id: Mapped[int] = mapped_column(RowId, nullable=False)
    person_id: Mapped[int] = mapped_column(RowId, nullable=False)
    view: Mapped[str] = mapped_column(Text, nullable=False)
    x: Mapped[float] = mapped_column(Float, nullable=False)
    y: Mapped[float] = mapped_column(Float, nullable=False)
    cluster_id: Mapped[int | None] = mapped_column(Integer)


class ProjectionCluster(Base):
    __tablename__ = "projection_clusters"
    __table_args__ = (PrimaryKeyConstraint("run_id", "view", "cluster_index"),)

    run_id: Mapped[int] = mapped_column(RowId, nullable=False)
    view: Mapped[str] = mapped_column(Text, nullable=False)
    cluster_index: Mapped[int] = mapped_column(Integer, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    field_name: Mapped[str | None] = mapped_column(Text)
    member_count: Mapped[int] = mapped_column(Integer, nullable=False)
    cx: Mapped[float] = mapped_column(Float, nullable=False)
    cy: Mapped[float] = mapped_column(Float, nullable=False)
    color_slot: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )


class ProjectionClusterEdge(Base):
    __tablename__ = "projection_cluster_edges"
    __table_args__ = (PrimaryKeyConstraint("run_id", "view", "source_cluster", "target_cluster"),)

    run_id: Mapped[int] = mapped_column(RowId, nullable=False)
    view: Mapped[str] = mapped_column(Text, nullable=False)
    source_cluster: Mapped[int] = mapped_column(Integer, nullable=False)
    target_cluster: Mapped[int] = mapped_column(Integer, nullable=False)
    collaboration_weight: Mapped[float | None] = mapped_column(Float)
    topic_weight: Mapped[float | None] = mapped_column(Float)
```

Note: the model previously omitted `similarity_group` even though schema.sql has it — leave that as-is; the `view`/`cluster_id` additions are the only changes here.

- [ ] **Step 4: Apply the migration**

```bash
.venv/bin/alembic upgrade head
.venv/bin/alembic current
```

Expected: `current` shows the new revision; no error.

- [ ] **Step 5: Verify the schema**

```bash
.venv/bin/python - <<'PY'
from api.deps import _SessionLocal
from sqlalchemy import text
with _SessionLocal() as s:
    for t in ("topics", "publication_topics", "person_topics", "projection_clusters", "projection_cluster_edges"):
        print(t, s.execute(text(f"SELECT count(*) FROM {t}")).scalar())
    cols = [r[0] for r in s.execute(text("SELECT column_name FROM information_schema.columns WHERE table_name='person_projections_2d'")).all()]
    print("person_projections_2d:", cols)
PY
```

Expected: all counts 0; `person_projections_2d` includes `view` and `cluster_id`.

- [ ] **Step 6: Commit**

```bash
git add db/migrations/versions/ db/models/
git commit -m "feat(db): atlas v2 topics and projection tables"
```

---

### Task 2b: Deterministic demo dataset generator

**Files:**
- Create: `scripts/db/seed_demo.py`
- Test: DB verification queries.

**Background (controller finding, fix round 1):** `db/seed.sql` is stale — it inserts TEXT ids (`'inst-uoft'`) but the current schema (via `schema.sql` + migration 9a20) uses `BIGINT GENERATED BY DEFAULT AS IDENTITY` for every id column; all its INSERTs fail on a fresh DB. It cannot be used for local pipeline verification. This task replaces its role with a deterministic generator consistent with the current schema. (The real pilot data lives in the user's Neon DB and arrives via backfill; this is verification data only.)

**Interfaces:**
- Consumes: `db.config.get_database_url()` / `api.deps._SessionLocal`, tables from Tasks 2/2b.
- Produces: a populated local DB consumed by T3 verification, T6 verification, T8 (`build_atlas`), T9 (API), T12 (pilot tuning).

- [ ] **Step 1: Write the generator**

Create `scripts/db/seed_demo.py` (run: `python3 -m scripts.db.seed_demo`). Spec:

- Idempotent: delete rows in dependency order for every table it fills (people, person_aliases, person_affiliations, affiliation_org_assignments, organizations, org_relationships, publications, publication_authors, concepts, person_concepts, publication_concepts, topics, publication_topics, person_topics) then re-insert. Use `DELETE FROM` (no TRUNCATE, matviews block truncate).
- Org tree: 2 universities (`University of California, Berkeley`, `Stanford University`, kind 'university') → each with 2 children (Berkeley: `Haas School of Business` kind 'school', `Department of Economics` kind 'department'; Stanford: `Graduate School of Business` kind 'school', `Department of Economics` kind 'department') → 2 labs under each school (kind 'lab'). Wire via `org_relationships` (relationship_type 'primary', validity `[starts_at, ends_at)` with NULL ends; use the `VALIDITY_EXPRESSION` convention from db/models/base.py). Also insert `org_tree_current` refresh at the end.
- People: exactly 60, deterministic names (hard-coded lists, first/last arrays indexed by id — NO randomness), spread 15 per unit; `position_rank` from the enum values used in `db/models/enums.py`; `claimed_status` 'unclaimed'. Validity: ~50 active (validity contains CURRENT_DATE), 10 retired (ended 2020-01-01..2023-01-01).
- Affiliations: one chart_anchor per person via `person_affiliations` (starts_at/ends_at matching validity) + `affiliation_org_assignments` with `assignment_type='chart_anchor'` (this feeds the `person_anchor` materialized view).
- Publications: 180, titles "Demo paper N of <author list topic>" (deterministic), years 2012-2026, cited_by_count deterministic. Authorship patterns (these matter for the layout):
  - Pair A: person 1 & 2 share 18 papers (super-pair, "Nakamura/Steinsson" style).
  - Pair B: person 3 & 4 share 14 papers (tax, "Saez/Zucman" style).
  - Hub: person 5 coauthors ≥1 paper with 14 different people (30 papers total with them).
  - Three cliques of 5 people: intra-clique coauthorship ≥8 papers per pair (i.e., each pair shares ≥8 pubs); one clique = finance, one = macro, one = labor.
  - Bridge: person 21 coauthors 4 papers with clique-1 members and 4 with clique-2 members, few or none within either clique.
  - 10 people with 0 publications (isolates).
  - Multi-author papers: ~30 papers with k=4..6 authors (to exercise fractional weights).
  - Every paper's author list is deterministic (predefined per-paper author id tuples).
- Concepts: 12 (level 1 and 2, e.g. "Macroeconomics" level 1, "Monetary Policy" level 2). `person_concepts`: top 8 per person with scores 0.2-0.95 matching their field; `publication_concepts` for each paper (2-4 concepts, scores).
- Topics: 10 topics in `topics` table (openalex_topic_id 'T10001'..'T10010', display_name like "Macroeconomics · Monetary Policy", subfield_name/field_name/domain_name set so field-level profiles differentiate: fields "Economics, Econometrics and Finance", "Business, Management and Accounting", "Psychology", "Decision Sciences"); `publication_topics` 2-3 per paper (score 0.3-0.9, is_primary for the top); then aggregate `person_topics` with the same SQL as `scripts/backfill/topics.py::rebuild_person_topics`.
- Refresh materialized views at the end: `person_coauthor_edges`, `person_anchor`, `org_current_roster`, `org_tree_current` (`REFRESH MATERIALIZED VIEW` for each; org_tree_current may need a recursive build — if the view's definition requires it, use the same refresh as the app does; if refresh fails, report it).
- Determinism: no RNG anywhere; all values from fixed lists/arithmetic. Two runs must produce identical row counts.
- Print a summary at the end: people, publications, publication_authors, person_coauthor_edges, person_topics, topics counts.

- [ ] **Step 2: Run + verify**

Run: `python3 -m scripts.db.seed_demo`
Verify:
```sql
SELECT count(*) FROM people;                          -- 60
SELECT count(*) FROM person_coauthor_edges;           -- > 0
SELECT count(*) FROM person_topics;                   -- > 0
SELECT count(*) FROM topics;                          -- 10
SELECT count(*) FROM org_tree_current;                -- > 0
SELECT count(*) FROM person_anchor;                   -- 60
```
Run the generator TWICE — the second run must succeed (idempotent) with identical counts.

- [ ] **Step 3: Commit**

```bash
git add scripts/db/seed_demo.py
git commit -m "feat(db): deterministic demo dataset generator for local verification"
```

---

### Task 3: OpenAlex topics backfill

**Files:**
- Create: `scripts/backfill/topics.py`
- Modify: `scripts/backfill/openalex.py` (keyless mode — see Step 0)
- Test: verification against the local DB (keyless anonymous mode; a key in `.env` is optional and only raises the rate pool).

- [ ] **Step 0: Keyless OpenAlex mode (no user input needed)**

`OPENALEX_API_KEY` may be absent. OpenAlex officially supports anonymous access (lower rate tier). Change `resolve_api_key` to return `""` instead of raising when unset, and make `OpenAlexClient` skip the `api_key` query param when empty, with a slower politeness interval for anonymous mode:

In `scripts/backfill/openalex.py`:

```python
def resolve_api_key(explicit: str | None = None) -> str:
    load_dotenv()
    return (explicit or os.environ.get("OPENALEX_API_KEY") or "").strip()
```

```python
class OpenAlexClient:
    def __init__(
        self,
        *,
        api_key: str | None = None,
        min_interval: float | None = None,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.api_key = resolve_api_key(api_key)
        # Anonymous tier is rate-limited harder; be polite by default.
        self.min_interval = min_interval or (MIN_INTERVAL if self.api_key else 0.35)
        self.timeout = timeout
        self._last_hit = 0.0
```

and in `_with_api_key`, skip appending when `self.api_key` is empty:

```python
    def _with_api_key(self, url: str) -> str:
        if not self.api_key:
            return url
        parsed = urllib.parse.urlparse(url)
        qs = urllib.parse.parse_qs(parsed.query, keep_blank_values=True)
        qs["api_key"] = [self.api_key]
        query = urllib.parse.urlencode(qs, doseq=True)
        return urllib.parse.urlunparse(parsed._replace(query=query))
```

Keep `MIN_INTERVAL` and the existing behavior when a key IS present. No other callers change.

**Interfaces:**
- Consumes: `scripts/backfill/openalex.py` (`OpenAlexClient`, `short_id`), `scripts/backfill/common.py` helpers (`sql_person_is_berkeley_anchored` if used), `db/config.py` via `api.deps._SessionLocal`.
- Produces: populated `topics`, `publication_topics`, `person_topics`; consumed by Task 6 loaders.

- [ ] **Step 1: Write the backfill script**

Create `scripts/backfill/topics.py`:

```python
"""Backfill OpenAlex topics onto publications and people.

Pipeline:
  1. For each publication with an OpenAlex external id, fetch the work and
     write its ``topics`` array (plus primary flag) into ``publication_topics``.
  2. Upsert topic lineage rows (subfield/field/domain names) into ``topics``.
  3. Aggregate each person's works into ``person_topics``.

Requires ``OPENALEX_API_KEY`` in ``.env``. Mirror ``concepts.py`` conventions.

    .venv/bin/python -m scripts.backfill.topics [--limit N] [--dry]
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal
from scripts.backfill.openalex import OpenAlexClient, short_id

# OpenAlex topic scores on works are 0-100; we store 0-1.
MIN_TOPIC_SCORE = 5.0
MAX_TOPICS_PER_WORK = 12


def _normalize_score(raw: Any) -> float:
    try:
        val = float(raw)
    except (TypeError, ValueError):
        return 0.0
    if val > 1.0:
        val /= 100.0
    return max(0.0, min(1.0, val))


def upsert_topic(session: Session, t: dict[str, Any]) -> str:
    tid = short_id(t.get("id"))
    if not tid:
        raise ValueError("topic missing OpenAlex id")
    sub = t.get("subfield") or {}
    field = t.get("field") or {}
    domain = t.get("domain") or {}
    session.execute(
        text(
            """
            INSERT INTO topics (openalex_topic_id, display_name, subfield_name, field_name, domain_name)
            VALUES (:tid, :name, :sub, :field, :domain)
            ON CONFLICT (openalex_topic_id) DO UPDATE SET
              display_name = EXCLUDED.display_name,
              subfield_name = EXCLUDED.subfield_name,
              field_name = EXCLUDED.field_name,
              domain_name = EXCLUDED.domain_name
            """
        ),
        {
            "tid": tid,
            "name": (t.get("display_name") or "").strip(),
            "sub": (sub.get("display_name") or "").strip() or None,
            "field": (field.get("display_name") or "").strip() or None,
            "domain": (domain.get("display_name") or "").strip() or None,
        },
    )
    return tid


def link_publication_topics(
    session: Session,
    publication_id: int,
    work: dict[str, Any],
) -> int:
    """Write topics for one work; return how many were linked."""
    raw = work.get("topics") or []
    if not raw and work.get("primary_topic"):
        raw = [work["primary_topic"]]
    primary_id = short_id((work.get("primary_topic") or {}).get("id"))
    count = 0
    for t in raw[:MAX_TOPICS_PER_WORK]:
        score = _normalize_score(t.get("score"))
        if score < MIN_TOPIC_SCORE / 100.0:
            continue
        tid = upsert_topic(session, t)
        session.execute(
            text(
                """
                INSERT INTO publication_topics (publication_id, topic_id, score, is_primary)
                VALUES (:pid, :tid, :score, :is_primary)
                ON CONFLICT DO NOTHING
                """
            ),
            {"pid": publication_id, "tid": tid, "score": score, "is_primary": tid == primary_id},
        )
        count += 1
    return count


def fetch_and_link(
    session: Session,
    client: OpenAlexClient,
    publication_id: int,
    work_id: str,
) -> int:
    """Fetch a work by its OpenAlex id and link its topics (idempotent)."""
    existing = session.execute(
        text(
            "SELECT count(*) FROM publication_topics WHERE publication_id = :pid"
        ),
        {"pid": publication_id},
    ).scalar()
    if existing:
        return 0
    work = client.get_json(f"/works/{work_id}")
    return link_publication_topics(session, publication_id, work)


def rebuild_person_topics(session: Session) -> None:
    """Aggregate publication topics into per-person profiles (idempotent rebuild)."""
    session.execute(text("DELETE FROM person_topics"))
    session.execute(
        text(
            """
            INSERT INTO person_topics (person_id, topic_id, score, works_count)
            SELECT
              pa.person_id,
              pt.topic_id,
              avg(coalesce(pt.score, 0.5))::real,
              count(DISTINCT pa.publication_id)::int
            FROM publication_authors pa
            JOIN publication_topics pt ON pt.publication_id = pa.publication_id
            GROUP BY pa.person_id, pt.topic_id
            """
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=0, help="max works to process (0 = all)")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    client = OpenAlexClient()
    with _SessionLocal() as session:
        rows = session.execute(
            text(
                """
                SELECT p.id, e.external_id
                FROM publications p
                JOIN external_identifiers e
                  ON e.publication_id = p.id AND e.provider = 'openalex'
                ORDER BY p.id
                """
            )
        ).all()
        if args.limit:
            rows = rows[: args.limit]
        print(f"[topics] {len(rows)} publications to process")
        done = linked = 0
        for pub_id, work_id in rows:
            done += 1
            linked += fetch_and_link(session, client, int(pub_id), str(work_id))
            if done % 50 == 0:
                print(f"[topics] {done}/{len(rows)} works, {linked} topic links")
        if args.dry:
            print("[topics] --dry: skipping person aggregation and commit")
            return
        rebuild_person_topics(session)
        session.commit()
        print("[topics] done — person_topics rebuilt")


if __name__ == "__main__":
    main()
```

- [ ] **Step 2: Verify against the DB**

```bash
OPENALEX_API_KEY=<your key in .env already> .venv/bin/python -m scripts.backfill.topics --limit 30 --dry
```

Expected: prints progress; each fetched work either skips (already linked) or links topics.

Then run the full backfill:

```bash
.venv/bin/python -m scripts.backfill.topics
```

- [ ] **Step 3: Verify aggregation**

```bash
.venv/bin/python - <<'PY'
from api.deps import _SessionLocal
from sqlalchemy import text
with _SessionLocal() as s:
    print("topics:", s.execute(text("SELECT count(*) FROM topics")).scalar())
    print("publication_topics:", s.execute(text("SELECT count(*) FROM publication_topics")).scalar())
    print("person_topics:", s.execute(text("SELECT count(*) FROM person_topics")).scalar())
    print("people with topics:", s.execute(text("SELECT count(DISTINCT person_id) FROM person_topics")).scalar())
    print("sample:", s.execute(text("""
        SELECT pt.person_id, pt.topic_id, round(pt.score::numeric, 3), pt.works_count
        FROM person_topics pt ORDER BY pt.works_count DESC LIMIT 5""")).all())
PY
```

Expected: nonzero counts; sample rows show topics with works_count > 0.

- [ ] **Step 4: Commit**

```bash
git add scripts/backfill/topics.py
git commit -m "feat(backfill): OpenAlex topics -> person_topics"
```

---

### Task 4: atlas_core — normalization, backbone, communities

**Files:**
- Create: `scripts/embed/atlas_core.py`
- Test: `tests/test_atlas_core.py`

**Interfaces:**
- Consumes: nothing but numpy/scipy/igraph.
- Produces (exact signatures used by later tasks):
  - `collapse_edges(edges: list[tuple[int,int,float]], n: int) -> dict[tuple[int,int], float]`
  - `association_strength(n: int, edges: list[tuple[int,int,float]]) -> np.ndarray` (n×n)
  - `disparity_filter(edges: list[tuple[int,int,float]], n: int, alpha: float = 0.05) -> list[tuple[int,int,float]]`
  - `leiden_communities(n: int, edges: list[tuple[int,int,float]], resolution: float = 1.0, seed: int = 42) -> list[int]`
  - `sweep_resolution(edges: list[tuple[int,int,float]], n: int, target_min: int, target_max: int, *, seed: int = 42) -> tuple[list[int], float]`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_atlas_core.py`:

```python
import numpy as np

from scripts.embed.atlas_core import (
    association_strength,
    collapse_edges,
    disparity_filter,
    leiden_communities,
    sweep_resolution,
)


def test_collapse_edges_sums_parallel_edges():
    out = collapse_edges([(0, 1, 1.0), (1, 0, 2.0), (1, 2, 3.0)], 3)
    assert out == {(0, 1): 3.0, (1, 2): 3.0}


def test_association_strength_downweights_hub_edges():
    # Star: node 0 is a hub with strength 10; leaf pair 1-2 has strength 2.
    edges = [(0, 1, 5.0), (0, 2, 5.0), (1, 2, 2.0)]
    a = association_strength(3, edges)
    # Leaf pair normalized similarity must exceed hub-leaf similarity.
    assert a[1, 2] > a[0, 1]
    assert np.isclose(a[0, 1], a[0, 2])
    assert np.allclose(a, a.T)


def test_disparity_filter_keeps_significant_edges():
    # Two cliques joined by one weak bridge: bridge should be pruned.
    edges = [
        (0, 1, 9.0), (0, 2, 9.0), (1, 2, 9.0),
        (3, 4, 9.0), (3, 5, 9.0), (4, 5, 9.0),
        (2, 3, 0.5),
    ]
    kept = disparity_filter(edges, 6, alpha=0.05)
    kept_set = {(min(i, j), max(i, j)) for i, j, _ in kept}
    assert (2, 3) not in kept_set
    assert (0, 1) in kept_set


def test_leiden_recovers_two_communities():
    # Two dense cliques connected by one edge.
    edges = (
        [(0, 1, 1.0), (0, 2, 1.0), (1, 2, 1.0)]
        + [(3, 4, 1.0), (3, 5, 1.0), (4, 5, 1.0)]
        + [(2, 3, 0.1)]
    )
    labels = leiden_communities(6, edges, resolution=1.0)
    assert labels[0] == labels[1] == labels[2]
    assert labels[3] == labels[4] == labels[5]
    assert labels[0] != labels[3]


def test_sweep_resolution_hits_target_range():
    # 3-clique chain: 15 nodes. Force a target of 2-3 clusters.
    edges = []
    for c in range(3):
        base = c * 5
        for i in range(base, base + 5):
            for j in range(i + 1, base + 5):
                edges.append((i, j, 1.0))
        if c < 2:
            edges.append((base + 4, base + 5, 0.05))
    labels, gamma = sweep_resolution(edges, 15, target_min=2, target_max=3)
    k = max(labels) + 1
    assert 2 <= k <= 3
    assert 0.0 < gamma
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_atlas_core.py -v`
Expected: failures (ModuleNotFoundError: atlas_core).

- [ ] **Step 3: Write the implementation**

Create `scripts/embed/atlas_core.py`:

```python
"""Pure atlas algorithms — no DB imports (unit-testable).

Steps that every atlas view shares:
  1. collapse parallel edges
  2. association-strength normalization (van Eck & Waltman 2009)
  3. disparity-filter backbone (Serrano et al. 2009)
  4. Leiden communities with a resolution sweep (Traag et al. 2019)
"""

from __future__ import annotations

import numpy as np


def collapse_edges(
    edges: list[tuple[int, int, float]], n: int
) -> dict[tuple[int, int], float]:
    """Sum parallel edges, canonical order (min, max), drop self-loops/zeros."""
    out: dict[tuple[int, int], float] = {}
    for a, b, w in edges:
        if a == b or w <= 0:
            continue
        if a < 0 or b < 0 or a >= n or b >= n:
            continue
        key = (min(a, b), max(a, b))
        out[key] = out.get(key, 0.0) + w
    return out


def association_strength(
    n: int, edges: list[tuple[int, int, float]]
) -> np.ndarray:
    """s_ij = 2m·w_ij / (k_i·k_j) — degree-normalized similarity (VOSviewer)."""
    m = sum(w for _, _, w in edges)
    strength = np.zeros(n, dtype=np.float64)
    for i, j, w in edges:
        strength[i] += w
        strength[j] += w
    sim = np.zeros((n, n), dtype=np.float64)
    for i, j, w in edges:
        denom = strength[i] * strength[j]
        if denom > 0:
            sim[i, j] = sim[j, i] = 2.0 * m * w / denom
    return sim


def disparity_filter(
    edges: list[tuple[int, int, float]],
    n: int,
    alpha: float = 0.05,
) -> list[tuple[int, int, float]]:
    """Keep edges whose normalized weight is significant at either endpoint.

    p_ij = w_ij / s_i; significance alpha_ij = (1 - p_ij)^(k_i - 1).
    OR rule (either endpoint significant). Isolated pairs are always kept so
    no component vanishes.
    """
    strength = np.zeros(n, dtype=np.float64)
    degree = np.zeros(n, dtype=np.int64)
    for i, j, w in edges:
        strength[i] += w
        strength[j] += w
        degree[i] += 1
        degree[j] += 1
    kept: list[tuple[int, int, float]] = []
    for i, j, w in edges:
        if degree[i] == 1 and degree[j] == 1:
            kept.append((i, j, w))
            continue
        sig_i = sig_j = 1.0
        if strength[i] > 0 and degree[i] > 1:
            p = w / strength[i]
            sig_i = (1.0 - p) ** (degree[i] - 1)
        if strength[j] > 0 and degree[j] > 1:
            p = w / strength[j]
            sig_j = (1.0 - p) ** (degree[j] - 1)
        if min(sig_i, sig_j) < alpha:
            kept.append((i, j, w))
    return kept


def leiden_communities(
    n: int,
    edges: list[tuple[int, int, float]],
    resolution: float = 1.0,
    seed: int = 42,
) -> list[int]:
    """Leiden modularity clustering (igraph C core)."""
    if n <= 1:
        return [0] * n
    import igraph as ig

    g = ig.Graph(n=n)
    if edges:
        g.add_edges([(i, j) for i, j, _ in edges])
        g.es["weight"] = [float(w) for _, _, w in edges]
    # igraph >= 1.0.0 signature: `resolution=` (not `resolution_parameter=`),
    # and no `seed` parameter (igraph's C core RNG is global).
    part = g.community_leiden(
        objective_function="modularity",
        weights="weight" if edges else None,
        resolution=float(resolution),
        n_iterations=2,
    )
    return list(part.membership)


def sweep_resolution(
    edges: list[tuple[int, int, float]],
    n: int,
    target_min: int,
    target_max: int,
    *,
    seed: int = 42,
    max_iterations: int = 12,
) -> tuple[list[int], float]:
    """Binary-search Leiden resolution to land cluster count in [min, max].

    Higher resolution -> more, smaller clusters.
    """
    lo, hi = 0.2, 8.0
    best_labels: list[int] = []
    best_gamma = 1.0
    best_gap: float | None = None
    target_mid = (target_min + target_max) / 2.0
    for _ in range(max_iterations):
        gamma = (lo + hi) / 2.0
        labels = leiden_communities(n, edges, resolution=gamma, seed=seed)
        k = max(labels) + 1
        gap = abs(k - target_mid)
        if best_gap is None or gap < best_gap:
            best_labels, best_gamma, best_gap = labels, gamma, gap
        if target_min <= k <= target_max:
            return labels, gamma
        if k < target_min:
            lo = gamma  # need more clusters -> raise resolution
        else:
            hi = gamma
    return (best_labels if best_labels else leiden_communities(n, edges, seed=seed)), best_gamma
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_atlas_core.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/embed/atlas_core.py tests/test_atlas_core.py
git commit -m "feat(embed): association strength, disparity filter, Leiden sweep"
```

---

### Task 5: atlas_core — layouts (linlog cluster-level, local member-level)

**Files:**
- Modify: `scripts/embed/atlas_core.py`
- Test: `tests/test_atlas_core.py`

**Interfaces:**
- Consumes: Task 4 functions.
- Produces (exact signatures used by Task 8):
  - `linlog_layout(n: int, edges: list[tuple[int,int,float]], *, iterations: int = 600, seed: int = 42) -> np.ndarray` (n×2, centered, unit bbox)
  - `local_spring_layout(n: int, edges: list[tuple[int,int,float]], *, iterations: int = 240, seed: int = 42) -> np.ndarray` (n×2, centered, max |coord| <= ~1)
  - `classical_mds(dist: np.ndarray, dim: int = 2) -> np.ndarray`
  - `normalize_to_canvas(pos: np.ndarray, span: float = 2.0) -> np.ndarray`
  - `assign_topic_clusters(dominant_field: np.ndarray, topic_profiles: np.ndarray, field_names: list[str], *, max_field_members: int = 60, members_per_cluster: int = 40) -> tuple[np.ndarray, list[str]]`
  - `place_bridge_nodes(pos: np.ndarray, labels: list[int], cluster_pos: np.ndarray, edges: list[tuple[int,int,float]], *, bridge_frac: float = 0.5, top2_share: float = 0.8) -> np.ndarray` (bridge_frac=0.5: only nodes whose external weight ≥ internal weight qualify — review-approved deviation from the draft's 0.3)
  - `cluster_topic_centroids(profiles: np.ndarray, labels: list[int]) -> np.ndarray` (k×T, L2-normalized)
- The topic-profile loader lives in Task 6; `assign_topic_clusters` here is pure (numpy only).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_atlas_core.py`:

```python
from scripts.embed.atlas_core import (
    assign_topic_clusters,
    classical_mds,
    linlog_layout,
    local_spring_layout,
    normalize_to_canvas,
)


def test_linlog_separates_two_cliques():
    edges = (
        [(0, 1, 1.0), (0, 2, 1.0), (1, 2, 1.0)]
        + [(3, 4, 1.0), (3, 5, 1.0), (4, 5, 1.0)]
        + [(2, 3, 0.1)]
    )
    pos = linlog_layout(6, edges, seed=0)
    intra = [np.linalg.norm(pos[i] - pos[j]) for i, j, _ in edges if min(i, j) < 3 <= max(i, j) or min(i, j) >= 3]
    inter = np.linalg.norm(pos[0] - pos[5])
    # Members of each clique stay tight; cliques drift apart.
    assert max(intra) < inter
    assert pos.shape == (6, 2)
    assert np.all(np.isfinite(pos))


def test_local_spring_keeps_strong_ties_close():
    edges = [(0, 1, 10.0), (1, 2, 10.0), (2, 0, 10.0), (3, 0, 0.2)]
    pos = local_spring_layout(4, edges, seed=0)
    d01 = np.linalg.norm(pos[0] - pos[1])
    d03 = np.linalg.norm(pos[0] - pos[3])
    assert d01 < d03


def test_classical_mds_recovers_line():
    x = np.array([[0.0], [1.0], [3.0], [6.0]])
    dist = np.abs(x - x.T)
    pos = classical_mds(dist, dim=2)
    # Rank order of pairwise distances preserved along the first axis.
    d = np.linalg.norm(pos[:, None, :] - pos[None, :, :], axis=2)
    assert d[0, 1] < d[0, 2] < d[0, 3]


def test_normalize_to_canvas_centers_and_scales():
    pos = np.array([[100.0, 100.0], [0.0, 0.0], [100.0, 0.0]])
    out = normalize_to_canvas(pos, span=2.0)
    assert np.allclose(out.mean(axis=0), 0.0, atol=1e-9)
    assert np.max(np.ptp(out, axis=0)) <= 2.0 + 1e-9


def test_assign_topic_clusters_refines_large_fields():
    # 80 people in field 0 (macro), 10 in field 1 (finance); profiles random-ish.
    rng = np.random.default_rng(0)
    n0, n1 = 80, 10
    dominant = np.array([0] * n0 + [1] * n1)
    prof = rng.normal(size=(n0 + n1, 6))
    prof[n0:] += np.array([0.0, 0, 0, 2.0, 2.0, 2.0])  # finance people cluster apart
    labels, names = assign_topic_clusters(
        dominant, prof, ["Macroeconomics", "Finance"], max_field_members=60
    )
    k = max(labels) + 1
    assert k >= 3  # field 0 split into 2+, field 1 kept whole
    assert len(names) == k
    assert set(labels[n0:]) == {labels[n0]}
    # Every label has at least one member
    for c in range(k):
        assert (labels == c).any()


def test_place_bridge_nodes_between_two_cliques():
    from scripts.embed.atlas_core import place_bridge_nodes

    # Node 6 ties cliques {0,1,2} and {3,4,5} equally and strongly.
    edges = (
        [(0, 1, 1.0), (0, 2, 1.0), (1, 2, 1.0)]
        + [(3, 4, 1.0), (3, 5, 1.0), (4, 5, 1.0)]
        + [(6, 0, 1.0), (6, 1, 1.0), (6, 2, 1.0),
           (6, 3, 1.0), (6, 4, 1.0), (6, 5, 1.0)]
    )
    labels = [0, 0, 0, 1, 1, 1, 0]  # 6 is assigned to clique 0
    cpos = np.array([[0.0, 0.0], [2.0, 0.0]])
    pos = np.zeros((7, 2))
    pos[6] = [0.5, 0.5]
    out = place_bridge_nodes(pos, labels, cpos, edges)
    # Pulled to the weighted barycenter of its neighbor clusters: (1, 0).
    assert abs(out[6, 0] - 1.0) < 1e-9
    assert abs(out[6, 1]) < 1e-9
    # Nodes without cross ties are untouched.
    assert np.allclose(out[:6], pos[:6])


def test_place_bridge_nodes_skips_multi_group_nodes():
    from scripts.embed.atlas_core import place_bridge_nodes

    # Node 4 ties one edge to each of 4 clusters: external weight spread
    # evenly (top-2 share 2/3 < 0.8) -> must stay put. A flat map cannot
    # hold a node with K overlapping memberships; edges carry that instead.
    labels = [0, 1, 2, 3, 0]
    cpos = np.array([[0.0, 0.0], [1.0, 0.0], [0.0, 1.0], [1.0, 1.0]])
    pos = np.zeros((5, 2))
    pos[4] = [9.0, 9.0]
    edges = [(4, 0, 1.0), (4, 1, 1.0), (4, 2, 1.0), (4, 3, 1.0)]
    out = place_bridge_nodes(pos, labels, cpos, edges)
    assert np.allclose(out[4], [9.0, 9.0])  # untouched


def test_cluster_topic_centroids_normalized():
    from scripts.embed.atlas_core import cluster_topic_centroids

    prof = np.array([[2.0, 0.0], [0.0, 3.0], [1.0, 1.0]])
    labels = [0, 0, 1]
    out = cluster_topic_centroids(prof, labels)
    assert out.shape == (2, 2)
    assert abs(np.linalg.norm(out[0]) - 1.0) < 1e-9
    assert out[0, 0] > out[0, 1]
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_atlas_core.py -v`
Expected: 5 failures (missing functions).

- [ ] **Step 3: Write the implementation**

Append to `scripts/embed/atlas_core.py`:

```python
# ---------------------------------------------------------------------------
# Layouts
# ---------------------------------------------------------------------------


def linlog_layout(
    n: int,
    edges: list[tuple[int, int, float]],
    *,
    iterations: int = 600,
    seed: int = 42,
) -> np.ndarray:
    """Force-directed layout with logarithmic attraction (linlog, Noack 2009).

    Attraction grows as log(1+d), so distant weak ties pull far less than in
    a linear model — communities separate into compact clusters.
    """
    rng = np.random.default_rng(seed)
    pos = rng.normal(scale=0.2, size=(n, 2))
    max_step = 0.15
    for _ in range(iterations):
        diff = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(diff, axis=2) + 1e-9
        np.fill_diagonal(dist, np.inf)
        inv = 1.0 / dist
        rep = np.zeros_like(pos)
        for i in range(n):
            rep[i] = (inv[i, :, None] ** 2 * (pos[i] - pos)).sum(axis=0)
        attr = np.zeros_like(pos)
        for i, j, w in edges:
            d = dist[i, j]
            u = (pos[i] - pos[j]) / d
            f = 0.02 * w * np.log1p(d) * u
            attr[i] -= f
            attr[j] += f
        force = 0.05 * rep + attr - 0.002 * pos
        step = force * 0.15
        norm = np.linalg.norm(step, axis=1, keepdims=True)
        step = np.where(
            norm > max_step, step * (max_step / np.maximum(norm, 1e-12)), step
        )
        pos += step
    pos -= pos.mean(axis=0)
    span = np.max(np.ptp(pos, axis=0))
    if span > 0:
        pos /= span
    return pos


def local_spring_layout(
    n: int,
    edges: list[tuple[int, int, float]],
    *,
    iterations: int = 240,
    seed: int = 42,
) -> np.ndarray:
    """Small intra-cluster layout; target distance shrinks with edge weight."""
    if n == 0:
        return np.zeros((0, 2))
    if n == 1:
        return np.zeros((1, 2))
    rng = np.random.default_rng(seed)
    pos = rng.normal(scale=0.3, size=(n, 2))
    for _ in range(iterations):
        diff = pos[:, None, :] - pos[None, :, :]
        dist = np.linalg.norm(diff, axis=2) + 1e-9
        np.fill_diagonal(dist, 0.0)
        rep = np.zeros_like(pos)
        close = (dist > 0) & (dist < 0.5)
        with np.errstate(divide="ignore", invalid="ignore"):
            rep = (
                -(np.where(close, (0.5 - dist) / dist, 0.0))[:, :, None] * diff * 0.02
            ).sum(axis=1)
        attr = np.zeros_like(pos)
        for i, j, w in edges:
            d = dist[i, j]
            target = 0.05 + 0.25 / (w + 0.3)
            u = (pos[j] - pos[i]) / d
            f = 0.06 * (d - target) * u
            attr[i] += f
            attr[j] -= f
        pos += (rep + attr) * 0.4
    pos -= pos.mean(axis=0)
    return pos


def classical_mds(dist: np.ndarray, dim: int = 2) -> np.ndarray:
    """Torgerson MDS on a distance matrix."""
    n = dist.shape[0]
    if n < 2:
        return np.zeros((n, max(dim, 2)))
    d2 = dist.astype(np.float64) ** 2
    h = np.eye(n) - np.ones((n, n)) / n
    b = -0.5 * h @ d2 @ h
    vals, vecs = np.linalg.eigh(b)
    order = np.argsort(vals)[::-1]
    vals = np.maximum(vals[order][:dim], 0.0)
    vecs = vecs[:, order][:, :dim]
    return vecs * np.sqrt(vals)


def normalize_to_canvas(pos: np.ndarray, span: float = 2.0) -> np.ndarray:
    """Center and scale a layout to a square canvas of side ~= span."""
    out = pos.copy()
    out -= out.mean(axis=0)
    s = float(np.max(np.ptp(out, axis=0)))
    if s > 1e-9:
        out = out / s * (span * 0.88)
    return out


# ---------------------------------------------------------------------------
# Topic clustering
# ---------------------------------------------------------------------------


def assign_topic_clusters(
    dominant_field: np.ndarray,
    topic_profiles: np.ndarray,
    field_names: list[str],
    *,
    max_field_members: int = 60,
    members_per_cluster: int = 40,
) -> tuple[np.ndarray, list[str]]:
    """Coarse = dominant OpenAlex field; refine oversized fields by Ward.

    dominant_field[i] is an index into field_names, or -1 for no topics
    (they form one "Unknown" cluster). Returns (labels, cluster_names).
    """
    from scipy.cluster.hierarchy import fcluster, linkage
    from scipy.spatial.distance import pdist

    n = dominant_field.shape[0]
    labels = dominant_field.astype(np.int64).copy()
    next_id = int(labels.max() + 1) if labels.size else 0
    names: dict[int, str] = {}
    for f in np.unique(dominant_field[dominant_field >= 0]):
        members = np.flatnonzero(dominant_field == f)
        name = field_names[int(f)] if 0 <= int(f) < len(field_names) else "Unknown"
        if members.size <= max_field_members:
            names[int(f)] = name
            continue
        k = max(2, int(np.ceil(members.size / members_per_cluster)))
        prof = topic_profiles[members]
        tree = linkage(pdist(prof, metric="euclidean"), method="ward")
        sub = fcluster(tree, t=k, criterion="maxclust")
        for j, s in enumerate(np.unique(sub)):
            labels[members[sub == s]] = next_id + j
            names[next_id + j] = f"{name} · {j + 1}"
        next_id += k
    if np.any(dominant_field < 0):
        names[0] = "Unknown"
        labels[dominant_field < 0] = 0
    ordered = sorted(names.items())
    remap = {old: new for new, (old, _) in enumerate(ordered)}
    labels = np.array([remap[int(x)] for x in labels])
    cluster_names = [name for _, name in ordered]
    return labels, cluster_names


def place_bridge_nodes(
    pos: np.ndarray,
    labels: list[int],
    cluster_pos: np.ndarray,
    edges: list[tuple[int, int, float]],
    *,
    bridge_frac: float = 0.5,
    top2_share: float = 0.8,
) -> np.ndarray:
    """Move bridge nodes to the weighted barycenter of their neighboring
    cluster centroids.

    A node with strong ties to 1-2 foreign clusters would otherwise force
    those clusters together in a flat layout. Instead we keep the clusters
    separated and let the node sit between them — it lands in the overlap of
    the two hulls, which reads as "bridges these groups". Nodes whose
    external weight is spread over many clusters stay put: a flat 2D map
    cannot hold a node with K overlapping memberships; the weighted cluster
    edges carry that information instead.
    """
    n = pos.shape[0]
    out = pos.copy()
    neighbor: dict[int, list[tuple[int, float]]] = {i: [] for i in range(n)}
    for i, j, w in edges:
        neighbor[i].append((j, w))
        neighbor[j].append((i, w))
    for i in range(n):
        nbrs = neighbor[i]
        if not nbrs:
            continue
        total = sum(w for _, w in nbrs)
        own = sum(w for j, w in nbrs if labels[j] == labels[i])
        external = total - own
        if total <= 0 or external / total < bridge_frac:
            continue
        ext_by_cluster: dict[int, float] = {}
        for j, w in nbrs:
            c = labels[j]
            if c != labels[i]:
                ext_by_cluster[c] = ext_by_cluster.get(c, 0.0) + w
        if not ext_by_cluster:
            continue
        top2 = sum(sorted(ext_by_cluster.values(), reverse=True)[:2])
        if top2 / external < top2_share:
            continue
        accum = np.zeros(2)
        wsum = 0.0
        for j, w in nbrs:
            accum += w * cluster_pos[labels[j]]
            wsum += w
        out[i] = accum / wsum
    return out


def cluster_topic_centroids(profiles: np.ndarray, labels: list[int]) -> np.ndarray:
    """Mean topic profile per cluster, L2-normalized (for edge weights)."""
    k = max(labels) + 1
    out = np.zeros((k, profiles.shape[1]))
    for c in range(k):
        members = np.flatnonzero(np.array(labels) == c)
        if members.size:
            out[c] = profiles[members].mean(axis=0)
    norms = np.linalg.norm(out, axis=1, keepdims=True)
    out = out / np.maximum(norms, 1e-9)
    return out
```

Note on the `names[0]` default: if nobody has `dominant_field == 0` and nobody is Unknown, id 0 may not exist; the remap over `ordered` handles that — labels always land in `range(len(cluster_names))`.

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_atlas_core.py -v`
Expected: 10 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/embed/atlas_core.py tests/test_atlas_core.py
git commit -m "feat(embed): linlog/local-spring layouts, MDS, topic cluster assignment"
```

---

### Task 6: Topic profile loader (DB) + Topic view build

**Files:**
- Create: `scripts/embed/topic_profiles.py` (DB loader, no layout logic)
- Test: `tests/test_atlas_core.py` (a pure reshape helper) + DB verification

**Interfaces:**
- Consumes: `person_topics`, `topics` (Task 2/3).
- Produces:
  - `load_topic_profiles(session, people: list[int]) -> tuple[np.ndarray, np.ndarray, list[str]]`
    → `(topic_profile n×T, field_profile n×F, field_names)`; profiles are IDF-weighted then L2-normalized; zero rows stay zero.

- [ ] **Step 1: Write the implementation**

Create `scripts/embed/topic_profiles.py`:

```python
"""Load per-person OpenAlex topic profiles from the DB.

Returns TF-IDF-weighted, L2-normalized matrices over topics and fields.
Zero rows (people with no topics) stay zero — callers route them to the
Unknown cluster.
"""

from __future__ import annotations

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session


def load_topic_profiles(
    session: Session, people: list[int]
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """→ (topic_profile n×T, field_profile n×F, field_names)."""
    n = len(people)
    if n == 0:
        return np.zeros((0, 0)), np.zeros((0, 0)), []
    rows = session.execute(
        text(
            """
            SELECT pt.person_id, pt.topic_id, pt.score, t.field_name
            FROM person_topics pt
            JOIN topics t ON t.openalex_topic_id = pt.topic_id
            WHERE pt.person_id = ANY(:ids)
            """
        ),
        {"ids": people},
    ).all()
    if not rows:
        return np.zeros((n, 0)), np.zeros((n, 0)), []

    p_idx = {pid: i for i, pid in enumerate(people)}
    topic_ids = sorted({r[1] for r in rows})
    t_idx = {tid: j for j, tid in enumerate(topic_ids)}
    mat = np.zeros((n, len(topic_ids)), dtype=np.float64)
    for person_id, tid, score, _field in rows:
        i, j = p_idx.get(int(person_id)), t_idx.get(tid)
        if i is not None and j is not None:
            mat[i, j] = max(mat[i, j], float(score))

    # IDF downweights ubiquitous topics (same trick as load_research_similarity).
    df = np.count_nonzero(mat > 0, axis=0).astype(np.float64)
    idf = np.log((n + 1.0) / (df + 1.0)) + 1.0
    mat *= idf
    norms = np.linalg.norm(mat, axis=1, keepdims=True)
    norms = np.maximum(norms, 1e-9)
    mat = mat / norms

    fields = sorted({r[3] for r in rows if r[3]})
    f_idx = {f: j for j, f in enumerate(fields)}
    fmat = np.zeros((n, len(fields)), dtype=np.float64)
    for person_id, _tid, score, field in rows:
        i, j = p_idx.get(int(person_id)), f_idx.get(field)
        if i is not None and j is not None and field:
            fmat[i, j] += float(score)
    fn = np.linalg.norm(fmat, axis=1, keepdims=True)
    fn = np.maximum(fn, 1e-9)
    fmat = fmat / fn
    return mat, fmat, fields
```

- [ ] **Step 2: Add a pure reshape test (dominant-field extraction)**

Append to `tests/test_atlas_core.py`:

```python
def test_dominant_field_extraction():
    from scripts.embed.atlas_core import dominant_field_from_profiles

    fmat = np.zeros((3, 2))
    fmat[0] = [1.0, 0.2]
    fmat[1] = [0.1, 1.0]
    fmat[2] = [0.0, 0.0]
    dom = dominant_field_from_profiles(fmat)
    assert dom.tolist() == [0, 1, -1]
```

Add to `scripts/embed/atlas_core.py`:

```python
def dominant_field_from_profiles(field_profile: np.ndarray) -> np.ndarray:
    """Argmax field per person; -1 for people with no profile."""
    n = field_profile.shape[0]
    dom = np.full(n, -1, dtype=np.int64)
    has = field_profile.sum(axis=1) > 1e-9
    dom[has] = field_profile[has].argmax(axis=1)
    return dom
```

- [ ] **Step 3: Run tests**

Run: `.venv/bin/python -m pytest tests/test_atlas_core.py -v`
Expected: 11 passed.

- [ ] **Step 4: Verify loader against the DB**

```bash
.venv/bin/python - <<'PY'
from api.deps import _SessionLocal
from sqlalchemy import text
from scripts.embed.topic_profiles import load_topic_profiles

with _SessionLocal() as s:
    people = [int(r[0]) for r in s.execute(text(
        "SELECT id FROM people ORDER BY id LIMIT 50")).all()]
    t, f, fields = load_topic_profiles(s, people)
    print("topic profile:", t.shape, "field profile:", f.shape, "fields:", fields[:6])
    print("people with topics:", int((t.sum(axis=1) > 0).sum()))
PY
```

Expected: sensible shapes (`(50, T)`, `(50, F)`), nonzero covered count when topics were backfilled.

- [ ] **Step 5: Commit**

```bash
git add scripts/embed/topic_profiles.py scripts/embed/atlas_core.py tests/test_atlas_core.py
git commit -m "feat(embed): topic profile loader with TF-IDF + field profiles"
```

---

### Task 7: metrics.py + gold set

**Files:**
- Create: `scripts/embed/metrics.py`
- Test: `tests/test_metrics.py`

**Interfaces:**
- Consumes: numpy arrays from Task 8.
- Produces (exact signatures):
  - `cluster_purity(cluster_ids: np.ndarray, field_ids: np.ndarray) -> float`
  - `separation_ratio(pos: np.ndarray, cluster_ids: np.ndarray) -> float`
  - `strong_edge_fidelity(pos: np.ndarray, edges: list[tuple[int,int,float]], top_frac: float = 0.1) -> float`
  - `overlap_rate(pos: np.ndarray, cluster_ids: np.ndarray, min_sep: float = 0.02) -> float`
  - `gold_set_distances(pos: np.ndarray, index_by_lastname: dict[str, int], gold_pairs: list[tuple[str, str]]) -> dict[tuple[str, str], float]`

- [ ] **Step 1: Write the failing tests**

Create `tests/test_metrics.py`:

```python
import numpy as np

from scripts.embed.metrics import (
    cluster_purity,
    gold_set_distances,
    overlap_rate,
    separation_ratio,
    strong_edge_fidelity,
)


def test_cluster_purity_weighted_mean():
    clusters = np.array([0, 0, 0, 1, 1])
    fields = np.array([0, 0, 1, 1, 1])
    # cluster 0: 2/3 pure, size 3; cluster 1: 3/3 pure, size 2 -> (2/3*3 + 1*2)/5
    assert abs(cluster_purity(clusters, fields) - (2.0 / 3.0 * 3 + 2) / 5) < 1e-9


def test_separation_ratio_above_one_for_separated_blobs():
    rng = np.random.default_rng(0)
    pos = np.vstack([
        rng.normal(0, 0.05, (20, 2)),
        rng.normal(2, 0.05, (20, 2)),
    ])
    clusters = np.array([0] * 20 + [1] * 20)
    assert separation_ratio(pos, clusters) > 3.0


def test_strong_edge_fidelity_prefers_short_edges():
    pos = np.array([[0.0, 0.0], [0.05, 0.0], [1.0, 0.0], [1.05, 0.0]])
    edges = [(0, 1, 10.0), (2, 3, 10.0), (0, 2, 1.0)]
    # top-1/3 by weight = (0,1) and (2,3), both ~0.05 long
    f = strong_edge_fidelity(pos, edges, top_frac=1 / 3)
    assert f < 0.1


def test_overlap_rate_counts_cross_cluster_near_pairs():
    pos = np.array([[0.0, 0.0], [0.005, 0.0], [5.0, 0.0]])
    clusters = np.array([0, 0, 1])
    assert overlap_rate(pos, clusters, min_sep=0.02) == 0.0
    clusters2 = np.array([0, 1, 1])
    assert overlap_rate(pos, clusters2, min_sep=0.02) == 1.0


def test_gold_set_distances_missing_people_skipped():
    pos = np.zeros((3, 2))
    pos[1] = [0.5, 0.0]
    index = {"naka": 0, "stein": 1, "saez": 2}
    out = gold_set_distances(pos, index, [("naka", "stein"), ("saez", "zucman")])
    assert ("naka", "stein") in out
    assert ("saez", "zucman") not in out
```

- [ ] **Step 2: Run to verify they fail**

Run: `.venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: failures (module missing).

- [ ] **Step 3: Write the implementation**

Create `scripts/embed/metrics.py`:

```python
"""Atlas layout quality metrics.

These replace recall@10/Spearman as the primary quality signals: they measure
readability and semantic separation rather than reproduction of a degenerate
all-pairs matrix. All functions are pure numpy — no DB access.
"""

from __future__ import annotations

import numpy as np


def cluster_purity(cluster_ids: np.ndarray, field_ids: np.ndarray) -> float:
    """Size-weighted mean of each cluster's dominant-field share."""
    total = 0.0
    count = 0
    for c in np.unique(cluster_ids):
        lab = field_ids[cluster_ids == c]
        if lab.size == 0:
            continue
        dom = np.bincount(lab).max() / lab.size
        total += dom * lab.size
        count += lab.size
    return float(total / count) if count else 0.0


def separation_ratio(pos: np.ndarray, cluster_ids: np.ndarray) -> float:
    """Median inter-cluster distance / median intra-cluster distance."""
    n = pos.shape[0]
    if n < 2:
        return 1.0
    dist = np.linalg.norm(pos[:, None, :] - pos[None, :, :], axis=2)
    iu = np.triu_indices(n, k=1)
    d = dist[iu]
    ci, cj = cluster_ids[iu[0]], cluster_ids[iu[1]]
    inter = np.median(d[ci != cj]) if np.any(ci != cj) else 0.0
    intra = np.median(d[ci == cj]) if np.any(ci == cj) else 1e-9
    return float(inter / max(intra, 1e-9))


def strong_edge_fidelity(
    pos: np.ndarray,
    edges: list[tuple[int, int, float]],
    top_frac: float = 0.1,
) -> float:
    """Mean 2D distance of the strongest edges — lower is better."""
    if not edges:
        return 0.0
    top = sorted(edges, key=lambda t: -t[2])[: max(1, int(len(edges) * top_frac))]
    ds = [float(np.linalg.norm(pos[i] - pos[j])) for i, j, _ in top]
    return float(np.mean(ds)) if ds else 0.0


def overlap_rate(
    pos: np.ndarray,
    cluster_ids: np.ndarray,
    min_sep: float = 0.02,
) -> float:
    """Fraction of cross-cluster pairs closer than `min_sep` among all close pairs."""
    n = pos.shape[0]
    if n < 2:
        return 0.0
    dist = np.linalg.norm(pos[:, None, :] - pos[None, :, :], axis=2)
    iu = np.triu_indices(n, k=1)
    d = dist[iu]
    close = d < min_sep
    if not np.any(close):
        return 0.0
    cross = cluster_ids[iu[0]][close] != cluster_ids[iu[1]][close]
    return float(cross.mean())


def gold_set_distances(
    pos: np.ndarray,
    index_by_lastname: dict[str, int],
    gold_pairs: list[tuple[str, str]],
) -> dict[tuple[str, str], float]:
    """2D distance for each gold pair whose members are on the map."""
    out: dict[tuple[str, str], float] = {}
    for a, b in gold_pairs:
        ia, ib = index_by_lastname.get(a), index_by_lastname.get(b)
        if ia is None or ib is None:
            continue
        out[(a, b)] = float(np.linalg.norm(pos[ia] - pos[ib]))
    return out
```

- [ ] **Step 4: Run to verify they pass**

Run: `.venv/bin/python -m pytest tests/test_metrics.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add scripts/embed/metrics.py tests/test_metrics.py
git commit -m "feat(embed): atlas quality metrics + gold set checker"
```

---

### Task 8: build_atlas.py — orchestration + run writer

**Files:**
- Create: `scripts/embed/build_atlas.py`
- Modify: `db/models/projection.py` (nothing new — models done in Task 2)
- Test: DB verification + dry-run.

**Interfaces:**
- Consumes: Task 4/5 core functions, Task 6 loader, Task 7 metrics, `api.deps._SessionLocal`, existing `person_coauthor_edges`/`person_relationships`/`people`.
- Produces: one active `embedding_runs` row (kind `person_atlas_v2`) with `person_projections_2d` (per-view rows), `projection_clusters`, `projection_cluster_edges`; consumed by Task 9 resolver.

- [ ] **Step 1: Write the implementation**

Create `scripts/embed/build_atlas.py`:

```python
"""Build the researcher atlas: two views, two-level layouts, weighted cluster edges.

Network view — communities from the disparity-filtered, association-strength
normalized coauthor graph; linlog cluster layout; local springs inside each
cluster footprint.
Topic view — OpenAlex topic profiles (person_topics); dominant-field coarse
clusters refined by Ward; MDS cluster layout on centroid topic distance;
local MDS inside each footprint.

Both views compute both inter-cluster edge semantics (collaboration weight =
sum of cross-cluster coauthor weight; topic weight = centroid cosine).

Run:
    .venv/bin/python -m scripts.embed.build_atlas [--view both|network|topic] [--dry]
"""

from __future__ import annotations

import argparse
import time

import numpy as np
from sqlalchemy import text
from sqlalchemy.orm import Session

from api.deps import _SessionLocal
from scripts.embed.atlas_core import (
    assign_topic_clusters,
    association_strength,
    classical_mds,
    cluster_topic_centroids,
    collapse_edges,
    disparity_filter,
    dominant_field_from_profiles,
    leiden_communities,
    linlog_layout,
    local_spring_layout,
    normalize_to_canvas,
    place_bridge_nodes,
    sweep_resolution,
)
from scripts.embed.metrics import (
    cluster_purity,
    gold_set_distances,
    overlap_rate,
    separation_ratio,
    strong_edge_fidelity,
)
from scripts.embed.topic_profiles import load_topic_profiles

KIND = "person_atlas_v2"
COAUTHOR_CAP = 20.0
ADVISOR_WEIGHT = 5.0
BACKBONE_ALPHA = 0.05
CANVAS_SPAN = 2.0
FOOTPRINT_A = 0.05
FOOTPRINT_B = 0.0035
# Known researcher pairs that must land close (regression check).
GOLD_PAIRS: list[tuple[str, str]] = [
    ("Nakamura", "Steinsson"),
    ("Saez", "Zucman"),
    ("DellaVigna", "Malmendier"),
    ("Einav", "Levin"),
]


def load_people(session: Session) -> list[int]:
    return [int(r[0]) for r in session.execute(text("SELECT id FROM people ORDER BY id")).all()]


def load_network_edges(session: Session, people: list[int]) -> list[tuple[int, int, float]]:
    """Coauthor edges with FRACTIONAL weights (papers are hyperedges).

    A paper with k authors contributes 1/(k-1) to each author pair instead of
    +1, so a 4-author paper doesn't inflate six independent pairwise
    relationships. Display paperCount stays the integer count from
    person_coauthor_edges (used by resolvers/frontend unchanged).
    """
    people_set = set(people)
    edges: list[tuple[int, int, float]] = []
    rows = session.execute(
        text(
            """
            SELECT pa1.person_id AS a, pa2.person_id AS b,
                   sum(1.0 / (pub.k - 1)) AS frac_weight
            FROM publication_authors pa1
            JOIN publication_authors pa2
              ON pa2.publication_id = pa1.publication_id
             AND pa2.person_id > pa1.person_id
            JOIN (
              SELECT publication_id, count(*) AS k
              FROM publication_authors
              GROUP BY publication_id
            ) pub ON pub.publication_id = pa1.publication_id
            WHERE pub.k > 1
            GROUP BY pa1.person_id, pa2.person_id
            """
        )
    ).all()
    for a, b, frac in rows:
        a, b = int(a), int(b)
        if a in people_set and b in people_set:
            edges.append((a, b, min(float(frac), COAUTHOR_CAP)))
    for a, b in session.execute(
        text(
            "SELECT from_person_id, to_person_id FROM person_relationships "
            "WHERE type = 'advised_by'"
        )
    ).all():
        if a in people_set and b in people_set:
            edges.append((int(a), int(b), ADVISOR_WEIGHT))
    return edges


def cluster_targets(n: int) -> tuple[int, int]:
    """Readable cluster count: ~1 per 40 people, 8-30 for small maps."""
    k = max(8, min(30, round(n / 40)))
    return max(2, k - 2), min(n, k + 2)


def _footprint(m: int) -> float:
    return FOOTPRINT_A + FOOTPRINT_B * float(np.sqrt(m))


def build_network_view(
    n: int,
    collapsed: dict[tuple[int, int], float],
    target_min: int,
    target_max: int,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, list[int], np.ndarray, np.ndarray, dict]:
    """→ (pos n×2, labels, cluster_pos k×2, collab k×k, gamma)"""
    edge_list = [(i, j, w) for (i, j), w in collapsed.items()]
    a = association_strength(n, edge_list)
    a_edges = [(i, j, float(a[i, j])) for (i, j) in collapsed]
    backbone = disparity_filter(a_edges, n, alpha=BACKBONE_ALPHA)
    if not backbone:  # degenerate fallback: keep strongest 3n edges
        backbone = sorted(a_edges, key=lambda t: -t[2])[: 3 * n]
    labels, gamma = sweep_resolution(backbone, n, target_min, target_max, seed=seed)
    k = max(labels) + 1

    collab = np.zeros((k, k))
    for (i, j), w in collapsed.items():
        ci, cj = labels[i], labels[j]
        if ci != cj:
            collab[ci, cj] += w
            collab[cj, ci] += w
    c_edges = [
        (s, t, collab[s, t])
        for s in range(k)
        for t in range(s + 1, k)
        if collab[s, t] > 0
    ]
    cpos = linlog_layout(k, c_edges, seed=seed)

    pos = np.zeros((n, 2))
    for c in range(k):
        members = np.array([i for i in range(n) if labels[i] == c], dtype=int)
        m = members.size
        if m == 1:
            pos[members[0]] = cpos[c]
            continue
        local_edges = [
            (int(np.where(members == i)[0][0]), int(np.where(members == j)[0][0]), w)
            for (i, j), w in collapsed.items()
            if labels[i] == c and labels[j] == c
        ]
        lpos = local_spring_layout(m, local_edges, seed=seed + c)
        radius = _footprint(m)
        span = float(np.max(np.abs(lpos))) if m > 1 else 1.0
        lpos = lpos / max(span, 1e-9) * radius
        pos[members] = cpos[c] + lpos

    # Bridge nodes: pull toward the weighted barycenter of neighboring
    # clusters so they read as "between groups" instead of forcing the
    # clusters together. Multi-group nodes stay put (edge weights carry
    # their relations).
    pos = place_bridge_nodes(pos, labels, cpos, edge_list)

    # Orphans (no edges at all): deterministic jitter around the global centroid.
    deg = np.zeros(n, dtype=int)
    for (i, j) in collapsed:
        deg[i] += 1
        deg[j] += 1
    if np.any(deg == 0):
        cx, cy = pos.mean(axis=0)
        rng = np.random.default_rng(seed + 999)
        for i in np.flatnonzero(deg == 0):
            angle = rng.uniform(0, 2 * np.pi)
            r = rng.uniform(0.05, 0.4)
            pos[i] = [cx + r * np.cos(angle), cy + r * np.sin(angle)]

    pos = normalize_to_canvas(pos, span=CANVAS_SPAN)
    return pos, labels, cpos, collab, gamma


def build_topic_view(
    n: int,
    topic_mat: np.ndarray,
    field_mat: np.ndarray,
    field_names: list[str],
    collapsed: dict[tuple[int, int], float],
    target_min: int,
    target_max: int,
    seed: int = 42,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """→ (pos n×2, labels, cluster_pos k×2, collab k×k, cluster_names)"""
    dominant = dominant_field_from_profiles(field_mat)
    labels, names = assign_topic_clusters(
        dominant, topic_mat, field_names, max_field_members=60
    )
    k = max(labels) + 1
    T = topic_mat.shape[1]
    centroids = np.zeros((k, T))
    for c in range(k):
        members = np.flatnonzero(np.array(labels) == c)
        if members.size:
            centroids[c] = topic_mat[members].mean(axis=0)
    cn = np.linalg.norm(centroids, axis=1, keepdims=True)
    cn = np.maximum(cn, 1e-9)
    centroids = centroids / cn
    cdist = 1.0 - centroids @ centroids.T
    cpos = classical_mds(cdist)

    pos = np.zeros((n, 2))
    for c in range(k):
        members = np.flatnonzero(np.array(labels) == c)
        m = members.size
        if m == 1:
            pos[members[0]] = cpos[c]
            continue
        sub = topic_mat[members]
        sd = 1.0 - sub @ sub.T
        lpos = classical_mds(sd)
        radius = _footprint(m)
        span = float(np.max(np.abs(lpos))) if m > 1 else 1.0
        lpos = lpos / max(span, 1e-9) * radius
        pos[members] = cpos[c] + lpos

    collab = np.zeros((k, k))
    for (i, j), w in collapsed.items():
        ci, cj = labels[i], labels[j]
        if ci != cj:
            collab[ci, cj] += w
            collab[cj, ci] += w
    pos = normalize_to_canvas(pos, span=CANVAS_SPAN)
    return pos, labels, cpos, collab, names


def topic_edge_weights(centroids: np.ndarray) -> np.ndarray:
    cn = np.linalg.norm(centroids, axis=1, keepdims=True)
    cn = np.maximum(cn, 1e-9)
    return (centroids / cn) @ (centroids / cn).T


def write_run(
    session: Session,
    people: list[int],
    views: dict[str, dict],
    notes: str,
) -> int:
    n = len(people)
    run_id = int(
        session.execute(
            text(
                """
                INSERT INTO embedding_runs (kind, algorithm, raw_dim, point_count, is_active, notes)
                VALUES (:kind, 'atlas_v2', 2, :count, FALSE, :notes)
                RETURNING id
                """
            ),
            {"kind": KIND, "count": n * len(views), "notes": notes},
        ).scalar_one()
    )
    for view, v in views.items():
        pos, labels, cluster_pos, collab, names, topic_centroids = (
            v["pos"], v["labels"], v["cluster_pos"], v["collab"], v["names"], v["topic_centroids"]
        )
        k = max(labels) + 1
        session.execute(
            text(
                """
                INSERT INTO person_projections_2d (run_id, person_id, view, x, y, cluster_id)
                SELECT :run, :pid, :view, :x, :y, :cid
                """
            ),
            [
                {"run": run_id, "pid": pid, "view": view, "x": float(pos[i, 0]), "y": float(pos[i, 1]), "cid": int(labels[i])}
                for i, pid in enumerate(people)
                if np.isfinite(pos[i, 0]) and np.isfinite(pos[i, 1])
            ],
        )
        members = np.bincount(labels, minlength=k)
        session.execute(
            text(
                """
                INSERT INTO projection_clusters
                  (run_id, view, cluster_index, label, field_name, member_count, cx, cy, color_slot)
                VALUES (:run, :view, :cidx, :label, :field, :members, :cx, :cy, :slot)
                """
            ),
            [
                {
                    "run": run_id,
                    "view": view,
                    "cidx": int(c),
                    "label": names[c],
                    "field": names[c].split(" · ")[0],
                    "members": int(members[c]),
                    "cx": float(cluster_pos[c][0]),
                    "cy": float(cluster_pos[c][1]),
                    "slot": int(c) % 12,
                }
                for c in range(k)
            ],
        )
        tw = topic_edge_weights(topic_centroids)
        session.execute(
            text(
                """
                INSERT INTO projection_cluster_edges
                  (run_id, view, source_cluster, target_cluster, collaboration_weight, topic_weight)
                VALUES (:run, :view, :s, :t, :collab, :topic)
                """
            ),
            [
                {
                    "run": run_id,
                    "view": view,
                    "s": int(s),
                    "t": int(t),
                    "collab": float(collab[s, t]),
                    "topic": float(tw[s, t]),
                }
                for s in range(k)
                for t in range(s + 1, k)
            ],
        )
    session.execute(
        text("UPDATE embedding_runs SET is_active = FALSE WHERE is_active AND id <> :new_id"),
        {"new_id": run_id},
    )
    session.execute(text("UPDATE embedding_runs SET is_active = TRUE WHERE id = :new_id"), {"new_id": run_id})
    session.commit()
    return run_id


def run_metrics(
    people: list[int],
    pos: np.ndarray,
    labels: list[int],
    collapsed: dict[tuple[int, int], float],
    field_ids: np.ndarray,
    lastnames: dict[int, str],
    view: str,
) -> list[str]:
    out: list[str] = []
    out.append(f"{view}: purity={cluster_purity(np.array(labels), field_ids):.3f}")
    out.append(f"{view}: sep={separation_ratio(pos, np.array(labels)):.2f}")
    edges = [(i, j, w) for (i, j), w in collapsed.items()]
    out.append(f"{view}: strong_edge_fidelity={strong_edge_fidelity(pos, edges):.4f}")
    out.append(f"{view}: overlap_rate={overlap_rate(pos, np.array(labels)):.3f}")
    index = {lastnames[people[i]].lower(): i for i in range(len(people))}
    gold = gold_set_distances(pos, index, [(a.lower(), b.lower()) for a, b in GOLD_PAIRS])
    for (a, b), d in gold.items():
        out.append(f"{view}: gold {a}-{b} dist={d:.4f}")
    return out


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--view", choices=["both", "network", "topic"], default="both")
    parser.add_argument("--dry", action="store_true")
    args = parser.parse_args()

    started = time.time()
    with _SessionLocal() as session:
        people = load_people(session)
        n = len(people)
        edges = load_network_edges(session, people)
        collapsed = collapse_edges(edges, n)
        target_min, target_max = cluster_targets(n)
        print(f"[atlas] {n} people, {len(collapsed)} edges, targets {target_min}-{target_max}")

        lastnames: dict[int, str] = {}
        for pid, last in session.execute(text("SELECT id, lastname FROM people")).all():
            if int(pid) in people:
                lastnames[int(pid)] = (last or "")

        topic_mat, field_mat, field_names = load_topic_profiles(session, people)
        field_ids = dominant_field_from_profiles(field_mat)
        field_ids = np.clip(field_ids, 0, max(len(field_names) - 1, 0))
        notes: list[str] = []
        views: dict[str, dict] = {}

        if args.view in ("both", "network"):
            pos, labels, cpos, collab, gamma = build_network_view(
                n, collapsed, target_min, target_max
            )
            names = [f"Community {c + 1}" for c in range(max(labels) + 1)]
            views["network"] = {
                "pos": pos,
                "labels": labels,
                "cluster_pos": cpos,
                "collab": collab,
                "names": names,
                "topic_centroids": cluster_topic_centroids(topic_mat, labels),
            }
            notes.append(f"network gamma={gamma:.3f}")
            notes += run_metrics(people, pos, labels, collapsed, field_ids, lastnames, "network")

        if args.view in ("both", "topic"):
            pos, labels, cpos, collab, names = build_topic_view(
                n, topic_mat, field_mat, field_names, collapsed, target_min, target_max
            )
            views["topic"] = {
                "pos": pos,
                "labels": labels,
                "cluster_pos": cpos,
                "collab": collab,
                "names": names,
                "topic_centroids": cluster_topic_centroids(topic_mat, labels),
            }
            notes += run_metrics(people, pos, labels, collapsed, field_ids, lastnames, "topic")

        if args.dry:
            print("[atlas] --dry: skipping DB writes")
            for line in notes:
                print(f"[atlas] {line}")
            return

        run_id = write_run(session, people, views, "; ".join(notes))
        print(f"[atlas] wrote run_id={run_id} in {time.time() - started:.1f}s")
        for line in notes:
            print(f"[atlas] {line}")


if __name__ == "__main__":
    main()
```

Known simplification: topic-view cluster names are set to "Unknown" in this file because `assign_topic_clusters` returns them — fix by using the returned `names`:

In `build_topic_view`, return `names` too, and in `main` use them:

```python
def build_topic_view(...) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, list[str]]:
    ...
    return pos, labels, centroids, collab, names
```

and

```python
            pos, labels, centroids, collab, names = build_topic_view(
                n, topic_mat, field_mat, field_names, collapsed, target_min, target_max
            )
            views["topic"] = {"pos": pos, "labels": labels, "centroids": centroids, "collab": collab, "names": names}
```

- [ ] **Step 2: Dry-run verification**

```bash
.venv/bin/python -m scripts.embed.build_atlas --view both --dry
```

Expected: prints people/edge counts, target range, per-view metric lines (purity, sep, fidelity, overlap, gold distances). No DB writes.

- [ ] **Step 3: Full run + DB verification**

```bash
.venv/bin/python -m scripts.embed.build_atlas --view both
.venv/bin/python - <<'PY'
from api.deps import _SessionLocal
from sqlalchemy import text
with _SessionLocal() as s:
    r = s.execute(text("SELECT id, algorithm, point_count FROM embedding_runs WHERE is_active")).one()
    print("active run:", r)
    for view in ("network", "topic"):
        print(view, "points:", s.execute(text(
            "SELECT count(*) FROM person_projections_2d WHERE run_id=:r AND view=:v"), {"r": r[0], "v": view}).scalar(),
            "clusters:", s.execute(text(
                "SELECT count(*) FROM projection_clusters WHERE run_id=:r AND view=:v"), {"r": r[0], "v": view}).scalar(),
            "edges:", s.execute(text(
                "SELECT count(*) FROM projection_cluster_edges WHERE run_id=:r AND view=:v"), {"r": r[0], "v": view}).scalar())
    print("cluster sample:", s.execute(text(
        "SELECT cluster_index, label, member_count FROM projection_clusters WHERE run_id=:r AND view='topic' ORDER BY member_count DESC LIMIT 5"), {"r": r[0]}).all())
PY
```

Expected: one active run; each view has ~n points, 8-30 clusters, cluster edges; labels like "Macroeconomics · 1" when topics are backfilled.

- [ ] **Step 4: Commit**

```bash
git add scripts/embed/build_atlas.py
git commit -m "feat(embed): two-view atlas pipeline with weighted cluster edges"
```

---

### Task 9: GraphQL schema + resolver

**Files:**
- Modify: `api/graphql/schema.graphql`
- Modify: `api/graphql/resolvers.py` (`resolve_projection`)
- Test: curl against a running API.

**Interfaces:**
- Consumes: Task 8 tables.
- Produces: `projection(view: String = "topic")` returning clusters/edges; consumed by Task 10/11 frontend.

- [ ] **Step 1: Extend the schema**

In `api/graphql/schema.graphql`, replace the `ProjectionPoint`/`Projection` block and the `projection` query field:

```graphql
type ProjectionPoint {
  id: ID!
  label: String!
  x: Float!
  y: Float!
  """Institution the person is anchored to."""
  institution: String
  """Anchor org id — used to cross-highlight org branches in the tree."""
  institutionId: ID
  """Coarse seniority rank."""
  rank: String
  """Visual prominence in [0, 1] — citations, publications, rank, network weight."""
  impact: Float!
  """Cluster this point belongs to in the active view (0-based)."""
  clusterId: Int
  """Human-readable cluster label (e.g. "Macroeconomics · 2")."""
  clusterLabel: String
  """When the person left the school's chart roster."""
  retiredAt: Date
  """Most recent publication year linked to this person, if any."""
  lastPublicationYear: Int
}

type ProjectionCluster {
  id: Int!
  label: String!
  fieldName: String
  memberCount: Int!
  cx: Float!
  cy: Float!
  colorSlot: Int!
}

type ProjectionClusterEdge {
  sourceCluster: Int!
  targetCluster: Int!
  """Sum of cross-cluster coauthor weight (normalized)."""
  collaborationWeight: Float
  """Cosine similarity between cluster topic centroids."""
  topicWeight: Float
}

type Projection {
  runId: ID!
  algorithm: String!
  view: String!
  pointCount: Int!
  points: [ProjectionPoint!]!
  clusters: [ProjectionCluster!]!
  edges: [ProjectionClusterEdge!]!
}
```

and in `Query`:

```graphql
  """
  All points/clusters/edges in the active atlas run for the requested view
  ("topic" or "network"). Cheap enough to send in one shot at school scale.
  """
  projection(view: String = "topic"): Projection!
```

- [ ] **Step 2: Rewrite `resolve_projection`**

In `api/graphql/resolvers.py`, replace the existing `resolve_projection` body with:

```python
@query.field("projection")
def resolve_projection(_obj, info, view: str = "topic") -> dict[str, Any]:
    view = view if view in ("topic", "network") else "topic"
    session = _session(info)
    active = session.execute(
        text(
            """
            SELECT id, algorithm, point_count
            FROM embedding_runs
            WHERE is_active
            """
        )
    ).mappings().first()
    if active is None:
        return {"runId": "", "algorithm": "", "view": view, "pointCount": 0, "points": [], "clusters": [], "edges": []}

    rows = session.execute(
        text(
            """
            SELECT
              p.person_id,
              p.x,
              p.y,
              p.cluster_id,
              c.label           AS cluster_label,
              pe.firstname,
              pe.middlename,
              pe.lastname,
              pa.title,
              pa.position_rank,
              pa.organization_id AS anchor_org_id,
              pa.validity,
              pa.ends_at,
              inst.id            AS institution_id,
              inst.name          AS institution_name,
              coalesce(impact.publication_count, 0) AS publication_count,
              coalesce(impact.citation_count, 0)    AS citation_count,
              impact.last_publication_year
            FROM person_projections_2d p
            JOIN people pe ON pe.id = p.person_id
            LEFT JOIN projection_clusters c
              ON c.run_id = p.run_id AND c.view = p.view AND c.cluster_index = p.cluster_id
            LEFT JOIN LATERAL (
              SELECT
                pa.title,
                pa.position_rank,
                pa.organization_id,
                pa.validity,
                paf.ends_at
              FROM person_anchor pa
              JOIN person_affiliations paf ON paf.id = pa.affiliation_id
              WHERE pa.person_id = p.person_id
              ORDER BY
                CASE WHEN pa.validity @> CURRENT_DATE THEN 0 ELSE 1 END,
                pa.is_primary DESC,
                upper(pa.validity) DESC NULLS FIRST,
                paf.starts_at DESC NULLS LAST
              LIMIT 1
            ) pa ON TRUE
            LEFT JOIN LATERAL (
              SELECT o.id, o.name
              FROM org_tree_current t
              JOIN organizations o ON o.id = ANY(t.ancestor_ids)
              WHERE t.organization_id = pa.organization_id
                AND o.kind = 'university'
              LIMIT 1
            ) inst ON TRUE
            LEFT JOIN LATERAL (
              SELECT
                count(*)::int AS publication_count,
                coalesce(sum(pub.cited_by_count), 0)::int AS citation_count,
                max(pub.publication_year)::int AS last_publication_year
              FROM publication_authors pa_pub
              JOIN publications pub ON pub.id = pa_pub.publication_id
              WHERE pa_pub.person_id = p.person_id
            ) impact ON TRUE
            WHERE p.run_id = :run_id AND p.view = :view
            """
        ),
        {"run_id": active["id"], "view": view},
    ).mappings().all()

    raw_impact: dict[str, float] = {}
    for r in rows:
        pid = encode("person", int(r["person_id"]))
        raw_impact[pid] = _raw_person_impact(
            int(r["citation_count"]),
            int(r["publication_count"]),
            r["position_rank"],
        )
    impact_by_id = _normalize_impacts(raw_impact)

    points = []
    for r in rows:
        anchor_row = {"validity": r.get("validity"), "ends_at": r.get("ends_at")}
        pid = encode("person", int(r["person_id"]))
        points.append(
            {
                "id": pid,
                "label": _full_name(r["firstname"], r["middlename"], r["lastname"]),
                "x": float(r["x"]),
                "y": float(r["y"]),
                "institution": r["institution_name"],
                "institutionId": (
                    encode("org", int(r["institution_id"]))
                    if r["institution_id"] is not None
                    else None
                ),
                "rank": r["position_rank"],
                "impact": impact_by_id[pid],
                "clusterId": int(r["cluster_id"]) if r["cluster_id"] is not None else None,
                "clusterLabel": r["cluster_label"],
                "retiredAt": _retired_at(anchor_row, date.today()),
                "lastPublicationYear": (
                    int(r["last_publication_year"])
                    if r.get("last_publication_year") is not None
                    else None
                ),
            }
        )

    clusters = [
        {
            "id": int(row["cluster_index"]),
            "label": row["label"],
            "fieldName": row["field_name"],
            "memberCount": int(row["member_count"]),
            "cx": float(row["cx"]),
            "cy": float(row["cy"]),
            "colorSlot": int(row["color_slot"]),
        }
        for row in session.execute(
            text(
                "SELECT cluster_index, label, field_name, member_count, cx, cy, color_slot "
                "FROM projection_clusters WHERE run_id = :run_id AND view = :view "
                "ORDER BY cluster_index"
            ),
            {"run_id": active["id"], "view": view},
        ).mappings().all()
    ]
    edges = [
        {
            "sourceCluster": int(row["source_cluster"]),
            "targetCluster": int(row["target_cluster"]),
            "collaborationWeight": row["collaboration_weight"],
            "topicWeight": row["topic_weight"],
        }
        for row in session.execute(
            text(
                "SELECT source_cluster, target_cluster, collaboration_weight, topic_weight "
                "FROM projection_cluster_edges WHERE run_id = :run_id AND view = :view"
            ),
            {"run_id": active["id"], "view": view},
        ).mappings().all()
    ]
    return {
        "runId": str(active["id"]),
        "algorithm": active["algorithm"],
        "view": view,
        "pointCount": len(points),
        "points": points,
        "clusters": clusters,
        "edges": edges,
    }
```

- [ ] **Step 3: Verify with the running API**

```bash
.venv/bin/uvicorn api.main:app --port 8000 &
sleep 2
curl -s 'http://localhost:8000/graphql' -H 'Content-Type: application/json' \
  -d '{"query":"query { projection(view: \"topic\") { view pointCount clusters { id label memberCount } edges { sourceCluster targetCluster collaborationWeight } points { id clusterId clusterLabel } } }"}' \
  | .venv/bin/python -m json.tool | head -60
```

Expected: `view: "topic"`, nonzero pointCount, clusters with labels, edges with weights, points with clusterId/clusterLabel. Repeat with `view: "network"`.

- [ ] **Step 4: Commit**

```bash
git add api/graphql/schema.graphql api/graphql/resolvers.py
git commit -m "feat(api): projection view argument with clusters and weighted edges"
```

---

### Task 10: Frontend data layer + pure helpers

**Files:**
- Modify: `src/api/projection.ts`
- Create: `src/lib/scatterLayout.ts`
- Create: `src/lib/scatterLayout.test.ts`
- Test: `npm test`

**Interfaces:**
- Consumes: Task 9 GraphQL.
- Produces: `ProjectionData` with `view`, `clusters`, `edges`; `clusterHulls(points, clusters)`, `edgeWidthScale(weight, max)`, `curvedEdgePath(a, b, sag)`; consumed by Task 11.

- [ ] **Step 1: Update the query and types**

Replace `src/api/projection.ts`:

```ts
import { gql } from "@apollo/client";

export const PROJECTION = gql`
  query Projection($view: String!) {
    projection(view: $view) {
      runId
      algorithm
      view
      pointCount
      points {
        id
        label
        x
        y
        institution
        institutionId
        rank
        impact
        clusterId
        clusterLabel
        retiredAt
        lastPublicationYear
      }
      clusters {
        id
        label
        fieldName
        memberCount
        cx
        cy
        colorSlot
      }
      edges {
        sourceCluster
        targetCluster
        collaborationWeight
        topicWeight
      }
    }
  }
`;

export interface ProjectionPoint {
  id: string;
  label: string;
  x: number;
  y: number;
  institution: string | null;
  institutionId: string | null;
  rank: string | null;
  impact: number;
  clusterId: number | null;
  clusterLabel: string | null;
  retiredAt: string | null;
  lastPublicationYear: number | null;
}

export interface ProjectionCluster {
  id: number;
  label: string;
  fieldName: string | null;
  memberCount: number;
  cx: number;
  cy: number;
  colorSlot: number;
}

export interface ProjectionClusterEdge {
  sourceCluster: number;
  targetCluster: number;
  collaborationWeight: number | null;
  topicWeight: number | null;
}

export interface Projection {
  runId: string;
  algorithm: string;
  view: string;
  pointCount: number;
  points: ProjectionPoint[];
  clusters: ProjectionCluster[];
  edges: ProjectionClusterEdge[];
}

export interface ProjectionData {
  projection: Projection;
}

export interface ProjectionVars {
  view: string;
}
```

- [ ] **Step 2: Create the pure helpers**

Create `src/lib/scatterLayout.ts`:

```ts
/**
 * Pure layout helpers for the atlas scatter: cluster hulls, edge scaling,
 * curved edge paths. No React, no network — unit-testable.
 */

export interface XY {
  x: number;
  y: number;
}

/** Andrew's monotone chain convex hull; returns CCW polygon. */
export function convexHull(points: XY[]): XY[] {
  const pts = [...points].sort((a, b) => a.x - b.x || a.y - b.y);
  if (pts.length <= 2) return pts;
  const cross = (o: XY, a: XY, b: XY) =>
    (a.x - o.x) * (b.y - o.y) - (a.y - o.y) * (b.x - o.x);
  const lower: XY[] = [];
  for (const p of pts) {
    while (lower.length >= 2 && cross(lower[lower.length - 2], lower[lower.length - 1], p) <= 0) {
      lower.pop();
    }
    lower.push(p);
  }
  const upper: XY[] = [];
  for (let i = pts.length - 1; i >= 0; i--) {
    const p = pts[i];
    while (upper.length >= 2 && cross(upper[upper.length - 2], upper[upper.length - 1], p) <= 0) {
      upper.pop();
    }
    upper.push(p);
  }
  lower.pop();
  upper.pop();
  return lower.concat(upper);
}

/** Hull polygon per cluster id; small clusters fall back to a 12-gon around the centroid. */
export function clusterHulls(
  points: Array<XY & { clusterId: number | null }>,
  clusters: Array<{ id: number; cx: number; cy: number }>,
): Map<number, XY[]> {
  const byCluster = new Map<number, XY[]>();
  for (const p of points) {
    if (p.clusterId == null) continue;
    const arr = byCluster.get(p.clusterId) ?? [];
    arr.push({ x: p.x, y: p.y });
    byCluster.set(p.clusterId, arr);
  }
  const out = new Map<number, XY[]>();
  for (const c of clusters) {
    const members = byCluster.get(c.id) ?? [];
    if (members.length < 3) {
      const r = 0.05 + 0.012 * members.length;
      const poly: XY[] = [];
      for (let i = 0; i < 12; i++) {
        const a = (i / 12) * Math.PI * 2;
        poly.push({ x: c.cx + r * Math.cos(a), y: c.cy + r * Math.sin(a) });
      }
      out.set(c.id, poly);
    } else {
      out.set(c.id, convexHull(members));
    }
  }
  return out;
}

/** Weight → stroke width factor in [0, 1]. */
export function edgeWidthScale(weight: number, maxWeight: number): number {
  if (maxWeight <= 0) return 0;
  return Math.max(0, Math.min(1, weight / maxWeight));
}

/** Quadratic bezier with a perpendicular sag for inter-cluster edges. */
export function curvedEdgePath(a: XY, b: XY, sag: number): string {
  const mx = (a.x + b.x) / 2;
  const my = (a.y + b.y) / 2;
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const len = Math.hypot(dx, dy) || 1;
  const px = -dy / len;
  const py = dx / len;
  const qx = mx + px * sag;
  const qy = my + py * sag;
  return `M ${a.x} ${a.y} Q ${qx} ${qy} ${b.x} ${b.y}`;
}
```

- [ ] **Step 3: Write the tests**

Create `src/lib/scatterLayout.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  clusterHulls,
  convexHull,
  curvedEdgePath,
  edgeWidthScale,
} from "./scatterLayout";

describe("convexHull", () => {
  it("returns the 4 corners of a square", () => {
    const hull = convexHull([
      { x: 0, y: 0 },
      { x: 1, y: 0 },
      { x: 1, y: 1 },
      { x: 0, y: 1 },
      { x: 0.5, y: 0.5 },
    ]);
    expect(hull).toHaveLength(4);
  });
});

describe("clusterHulls", () => {
  it("falls back to a polygon for tiny clusters", () => {
    const hulls = clusterHulls(
      [
        { x: 0, y: 0, clusterId: 1 },
        { x: 0.1, y: 0, clusterId: 1 },
      ],
      [{ id: 1, cx: 0.05, cy: 0, memberCount: 2, label: "x" }],
    );
    expect(hulls.get(1)!.length).toBe(12);
  });
});

describe("edgeWidthScale", () => {
  it("normalizes weight to 0..1", () => {
    expect(edgeWidthScale(2, 10)).toBeCloseTo(0.2);
    expect(edgeWidthScale(0, 10)).toBe(0);
    expect(edgeWidthScale(5, 0)).toBe(0);
  });
});

describe("curvedEdgePath", () => {
  it("produces a bezier through the sag point", () => {
    const path = curvedEdgePath({ x: 0, y: 0 }, { x: 2, y: 0 }, 0.5);
    expect(path.startsWith("M 0 0 Q")).toBe(true);
    expect(path).toContain("Q 1 0.5");
  });
});
```

- [ ] **Step 4: Run the tests**

Run: `npm test`
Expected: all pass (vitest picks up `src/lib/scatterLayout.test.ts`).

- [ ] **Step 5: Commit**

```bash
git add src/api/projection.ts src/lib/scatterLayout.ts src/lib/scatterLayout.test.ts
git commit -m "feat(web): atlas projection query, cluster types, hull/edge helpers"
```

---

### Task 11: Frontend — PeopleScatter dual views

**Files:**
- Modify: `src/components/PeopleScatter.tsx`
- Modify: `src/lib/scatterColor.ts`
- Test: manual verification (`npm run dev`).

**Interfaces:**
- Consumes: Task 10 types/helpers.
- Produces: the interactive atlas map.

- [ ] **Step 1: Color modes — "similarity" → "cluster"**

In `src/lib/scatterColor.ts`:

- Change `export type ScatterColorMode = "cluster" | "institution" | "focus";`
- `SCATTER_COLOR_MODES` → `[{ id: "cluster", label: "Cluster" }, { id: "institution", label: "Institution" }, { id: "focus", label: "Focus" }]`.
- Add `clusterColorSlot(slot: number): string` using the existing `paletteColor`; replace `colorForSimilarityGroup` calls with a `colorForCluster(point.clusterId, clusterByColorSlot)` mapping built from `clusters` (clusterId → `paletteColor(colorSlot)`).
- `loadColorMode`: accept `"cluster"`; map legacy `"similarity"` → `"cluster"`.
- `modeSubtitle("cluster", ...)` → `` `${clusters.length} clusters · size = publications & citations` `` (needs a cluster-count arg — pass `clusters.length`).

- [ ] **Step 2: View + edge state and query**

In `PeopleScatter.tsx`:

```ts
type AtlasView = "topic" | "network";
type EdgeType = "collaboration" | "topic";
const VIEW_STORAGE_KEY = "elmonte-scatter-view";
const EDGE_STORAGE_KEY = "elmonte-scatter-edge-type";
const DEFAULT_EDGE_COUNT = 30;

function loadView(): AtlasView {
  try {
    const raw = localStorage.getItem(VIEW_STORAGE_KEY);
    if (raw === "network" || raw === "topic") return raw;
  } catch {
    // ignore
  }
  return "topic";
}
```

Inside the component:

```ts
const [view, setView] = useState<AtlasView>(loadView);
const [edgeType, setEdgeType] = useState<EdgeType>(
  () => (localStorage.getItem(EDGE_STORAGE_KEY) === "collaboration" ? "collaboration" : "topic"),
);
const { data, loading, error } = useQuery<ProjectionData>(PROJECTION, {
  variables: { view },
  fetchPolicy: "cache-first",
});
const points = data?.projection.points ?? [];
const clusters = data?.projection.clusters ?? [];
const edges = data?.projection.edges ?? [];
```

(Guard `localStorage` access in a try/catch like `loadView` — shown as a lazy initializer above; wrap it in a `loadEdgeType()` helper if lint complains.)

- [ ] **Step 3: Edge rendering (weighted inter-cluster beziers)**

Add below the `coauthorLines` block (before points), gated by `!personalGraph`:

```tsx
const edgeList = useMemo(() => {
  const e = edges
    .filter((ed) => ed.sourceCluster !== ed.targetCluster)
    .map((ed) => ({
      ...ed,
      weight: edgeType === "collaboration" ? ed.collaborationWeight ?? 0 : ed.topicWeight ?? 0,
    }))
    .filter((ed) => ed.weight > 0)
    .sort((a, b) => b.weight - a.weight)
    .slice(0, DEFAULT_EDGE_COUNT);
  const maxW = e.length ? e[0].weight : 0;
  return { list: e, maxW };
}, [edges, edgeType]);
const clusterById = useMemo(
  () => new Map(clusters.map((c) => [c.id, c])),
  [clusters],
);
```

And in the SVG, inside `<g transform={worldTransform}>`, above the points (with `pointerEvents="none"`):

```tsx
{!personalGraph && edgeList.list.length > 0 && (
  <g className="people-scatter__cluster-edges" pointerEvents="none">
    {edgeList.list.map((ed) => {
      const a = clusterById.get(ed.sourceCluster);
      const b = clusterById.get(ed.targetCluster);
      if (!a || !b) return null;
      const t = edgeWidthScale(ed.weight, edgeList.maxW);
      const width = (1 + t * 6) * invScale;
      const opacity = 0.18 + t * 0.4;
      return (
        <path
          key={`${ed.sourceCluster}-${ed.targetCluster}`}
          d={curvedEdgePath({ x: a.cx, y: a.cy }, { x: b.cx, y: b.cy }, 0.04)}
          fill="none"
          stroke={edgeType === "collaboration" ? "#4338ca" : "#0f766e"}
          strokeWidth={width}
          strokeLinecap="round"
          opacity={opacity}
        />
      );
    })}
  </g>
)}
```

(Import `curvedEdgePath`, `edgeWidthScale` from `../lib/scatterLayout`.)

- [ ] **Step 4: Cluster hulls + labels + zoom**

Below the edges layer:

```tsx
{!personalGraph && (
  <g className="people-scatter__clusters" pointerEvents="none">
    {[...clusterHulls(points, clusters).entries()].map(([id, poly]) => {
      const c = clusterById.get(id);
      if (!c) return null;
      const pts = poly.map((p) => `${p.x},${p.y}`).join(" ");
      return (
        <polygon
          key={id}
          points={pts}
          fill={clusterColorSlot(c.colorSlot)}
          opacity={0.08}
          stroke={clusterColorSlot(c.colorSlot)}
          strokeWidth={1.2 * invScale}
          strokeOpacity={0.5}
        />
      );
    })}
  </g>
)}
```

Cluster labels as clickable buttons — render after the points group inside the world transform, or in screen space. Simplest: a screen-space overlay list of buttons positioned from world→screen; or reuse the existing pattern by rendering `<g>` with `pointerEvents="auto"` and a `text` plus transparent hit circle:

```tsx
{!personalGraph &&
  clusters.map((c) => {
    const members = points.filter((p) => p.clusterId === c.id);
    if (members.length === 0) return null;
    const xs = members.map((p) => p.x);
    const ys = members.map((p) => p.y);
    const lx = (Math.min(...xs) + Math.max(...xs)) / 2;
    const ly = (Math.min(...ys) + Math.max(...ys)) / 2;
    return (
      <g
        key={c.id}
        className="people-scatter__cluster-label"
        transform={`translate(${lx} ${ly}) scale(${invScale} ${-invScale})`}
        onClick={(e) => {
          e.stopPropagation();
          userPanned.current = true;
          setTransform(fitMembersTransform(members, size, transform.scale));
        }}
        style={{ cursor: "pointer" }}
      >
        <rect
          x={-72}
          y={-13}
          width={144}
          height={26}
          rx={13}
          fill="rgba(255,255,255,0.92)"
          stroke={clusterColorSlot(c.colorSlot)}
          strokeWidth={1.2}
        />
        <text textAnchor="middle" dy={4} className="people-scatter__cluster-label-text">
          {shortLabel(c.label, 24)} · {c.memberCount}
        </text>
      </g>
    );
  })}
```

Add the helper next to `fitAllTransform`:

```ts
function fitMembersTransform(
  members: ProjectionPoint[],
  size: { w: number; h: number },
  minScale: number,
): Transform {
  const t = fitAllTransform(members, size);
  return { ...t, scale: Math.max(t.scale, minScale) };
}
```

(Label pill sizes are rough; adjust `x/-72/144` to `shortLabel` length if desired.)

- [ ] **Step 5: Header controls**

In the header controls block (replacing the color-by select block), add the view and edge toggles:

```tsx
{!personalGraph && (
  <>
    <label className="people-scatter__view">
      <span>View</span>
      <select
        value={view}
        onChange={(e) => {
          const v = e.target.value as AtlasView;
          setView(v);
          try {
            localStorage.setItem(VIEW_STORAGE_KEY, v);
          } catch {
            // ignore
          }
        }}
        aria-label="Map view"
      >
        <option value="topic">Topic</option>
        <option value="network">Network</option>
      </select>
    </label>
    <label className="people-scatter__edge-type">
      <span>Edges</span>
      <select
        value={edgeType}
        onChange={(e) => {
          const v = e.target.value as EdgeType;
          setEdgeType(v);
          try {
            localStorage.setItem(EDGE_STORAGE_KEY, v);
          } catch {
            // ignore
          }
        }}
        aria-label="Cluster edge type"
      >
        <option value="collaboration">Collaboration</option>
        <option value="topic">Topic</option>
      </select>
    </label>
    <label className="people-scatter__color-by">
      <span>Color by</span>
      <select
        value={colorMode}
        onChange={(e) => onColorModeChange(e.target.value as ScatterColorMode)}
        aria-label="Color points by"
      >
        {SCATTER_COLOR_MODES.map((mode) => (
          <option key={mode.id} value={mode.id}>
            {mode.label}
          </option>
        ))}
      </select>
    </label>
  </>
)}
```

Update the subtitle to include the view and edge count:

```tsx
: `${points.length} researchers · ${view} view · ${edgeList.list.length} cluster edges`
```

- [ ] **Step 6: Manual verification**

```bash
npm run dev
```

Open `http://localhost:5173`. Verify:
1. Map shows cluster hulls with colored outlines + label pills ("Macroeconomics · 1 · 34" style) in Topic view.
2. Toggle View → Network: layout changes to communities; hulls/labels update.
3. Toggle Edges → Collaboration vs Topic: edge colors change (indigo vs teal), widths vary by weight.
4. Click a cluster label pill → map zooms to the cluster members; "Fit" restores overview.
5. Search + focus still works; personal network (coauthor ties) still renders with lines.
6. Scroll/pinch zoom, drag pan unchanged.

- [ ] **Step 7: Commit**

```bash
git add src/components/PeopleScatter.tsx src/lib/scatterColor.ts
git commit -m "feat(web): dual-view atlas with cluster hulls, labels, weighted edges"
```

---

### Task 12: Pilot tuning + docs

**Files:**
- Modify: `README.md`
- Modify: `.env.example` (comment for OPENALEX_API_KEY if missing)

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Re-run backfill with the real key**

```bash
.venv/bin/python -m scripts.backfill.topics
.venv/bin/python -m scripts.embed.build_atlas --view both
```

- [ ] **Step 2: Tune hyperparameters against the printed metrics**

Iterate on `scripts/embed/build_atlas.py` constants until reasonable targets:

| Metric | Target |
|---|---|
| purity (topic view) | ≥ 0.7 |
| separation_ratio (both views) | ≥ 2.5 |
| strong_edge_fidelity | ≤ 0.15 |
| overlap_rate | ≤ 0.05 |
| gold distances | Nakamura-Steinsson & Saez-Zucman < 0.35; others < 0.5 (tune by inspecting actual run, then tighten) |

Levers: `BACKBONE_ALPHA` (0.03–0.1), `FOOTPRINT_A/B` (cluster packing), `cluster_targets` counts, `members_per_cluster` (40–60), `max_field_members` (60–120). Each change: `.venv/bin/python -m scripts.embed.build_atlas --view both --dry` and compare metric lines. Do not ship numbers that look random — if a view is ugly, say so in the commit message and note the next knob to turn.

- [ ] **Step 3: Document the pipeline in README.md**

Add a section after "Run locally":

```markdown
## Atlas (people map) pipeline

The home scatter map is an offline two-view atlas:

1. `scripts/backfill/topics.py` — OpenAlex topics for each publication,
   aggregated into `person_topics` (topic id, score, works_count).
2. `scripts/embed/build_atlas.py` — builds both views in one run:
   - **network**: association-strength normalized coauthor graph →
     disparity-filter backbone → Leiden communities (resolution swept to a
     readable cluster count) → linlog cluster layout + intra-cluster springs;
     bridge nodes are placed at the weighted barycenter of their neighboring
     clusters (nodes tied to 1-2 foreign groups sit between the hulls;
     multi-group nodes stay in their primary cluster — the weighted cluster
     edges carry their relations).
   - **topic**: TF-IDF topic profiles → dominant OpenAlex field clusters
     (oversized fields refined by Ward) → MDS cluster layout on centroid
     topic distance + local MDS inside clusters.
   - Both views store inter-cluster edges with two weights: collaboration
     strength (sum of cross-cluster coauthor weight) and topic proximity
     (centroid cosine). The UI toggles between them.
   - Metrics (purity vs OpenAlex field, separation ratio, strong-edge
     fidelity, overlap rate, gold-set distances) are printed per run and
     stored in `embedding_runs.notes`.
3. The GraphQL `projection(view:)` query serves points + clusters + edges;
   the frontend renders hulls, labels, and weighted edges with zoom.

Layouts are deterministic (fixed seeds) and fully precomputed — the
frontend never computes positions.
```

- [ ] **Step 4: Commit**

```bash
git add README.md .env.example
git commit -m "docs: atlas pipeline, tuning guide"
```

---

### Task 13: Perspective mode (ego-centric polar view)

**Files:**
- Modify: `api/graphql/schema.graphql` (`PerspectiveAlter`, `Perspective`, query `perspective(personId:)`)
- Modify: `api/graphql/resolvers.py` (`resolve_perspective` + `_alter_groups`)
- Create: `src/api/perspective.ts` (query + types)
- Create: `src/lib/perspectiveLayout.ts` (pure: group ordering → theta, polar→cartesian, importance→radius)
- Create: `src/lib/perspectiveLayout.test.ts` (vitest)
- Modify: `src/components/PeopleScatter.tsx` (perspective render mode)

**Interfaces:**
- Consumes: `person_coauthor_edges`, `person_relationships` (`advised_by`), `publication_authors`, existing `_person_brief` helper.
- Produces: `perspective(personId)` → `{ focusId, alterCount, maxPaperCount, alters: [{ personId, label, institution, rank, paperCount, importance, group, hop, relation }] }`.

Design (per Design Principles): r = asymmetric importance `s_{P→A} = c_PA / totalPapers(P)`, linear radius; θ = alter-alter community groups (leiden on the alter subgraph, ≤40 nodes); hop 1 = direct tie, 2 = shared-coauthor only (opacity dimmed); top-40 cap with the rest counted in `alterCount`. Coordinates of the LANDSCAPE are never touched — perspective is a separate coordinate system.

- [ ] **Step 1: Schema additions**

In `api/graphql/schema.graphql` append:

```graphql
"""One alter in the ego-centric perspective of a focused person."""
type PerspectiveAlter {
  personId: ID!
  label: String!
  institution: String
  rank: String
  """Asymmetric importance to the focused person: papers-with-P / papers-of-P, in [0,1]."""
  importance: Float!
  """Alter-alter community group id (0-based) — used for the angular wedge."""
  group: Int!
  """1 = direct coauthor/advisor, 2 = shared-coauthor only."""
  hop: Int!
  paperCount: Int
  """`coauthor`, `advisor`, or `advisee`."""
  relation: String
}

type Perspective {
  focusId: ID!
  alterCount: Int!
  maxPaperCount: Int!
  alters: [PerspectiveAlter!]!
}
```

and in `Query`:

```graphql
  """Ego-centric perspective of `personId` — polar semantics (r = importance, θ = alter community)."""
  perspective(personId: ID!): Perspective!
```

- [ ] **Step 2: Resolver**

In `api/graphql/resolvers.py` append:

```python
PERSPECTIVE_ALTER_LIMIT = 40


def _alter_groups(session: Session, alter_ids: list[int]) -> dict[int, int]:
    """Leiden groups over the alter-alter shared-paper subgraph."""
    if len(alter_ids) <= 1:
        return {pid: 0 for pid in alter_ids}
    rows = session.execute(
        text(
            """
            SELECT person_a, person_b, paper_count
            FROM person_coauthor_edges
            WHERE person_a = ANY(:ids) AND person_b = ANY(:ids)
            """
        ),
        {"ids": alter_ids},
    ).all()
    import igraph as ig

    g = ig.Graph(n=len(alter_ids))
    idx = {pid: i for i, pid in enumerate(alter_ids)}
    es = [(idx[int(a)], idx[int(b)], float(c)) for a, b, c in rows]
    if es:
        g.add_edges([(a, b) for a, b, _ in es])
        g.es["weight"] = [w for _, _, w in es]
    part = g.community_leiden(
        objective_function="modularity",
        weights="weight" if es else None,
        resolution=1.0,
        n_iterations=2,
    )
    return {pid: int(c) for pid, c in zip(alter_ids, part.membership)}


@query.field("perspective")
def resolve_perspective(_obj, info, personId: str) -> dict[str, Any]:
    kind, row_id = decode(personId)
    if kind != "person":
        raise ValueError("perspective: personId must be a person id")
    session = _session(info)
    on = date.today()
    total = int(
        session.execute(
            text("SELECT count(*) FROM publication_authors WHERE person_id = :pid"),
            {"pid": row_id},
        ).scalar_one()
    )
    rows = session.execute(
        text(
            """
            SELECT CASE WHEN e.person_a = :pid THEN e.person_b ELSE e.person_a END AS other_id,
                   e.paper_count
            FROM person_coauthor_edges e
            WHERE e.person_a = :pid OR e.person_b = :pid
            """
        ),
        {"pid": row_id},
    ).all()
    alters: dict[int, dict[str, Any]] = {}
    for other_id, count in rows:
        other_id = int(other_id)
        s = float(count) / total if total else 0.0
        alters[other_id] = {
            "personId": encode("person", other_id),
            "paperCount": int(count),
            "importance": min(1.0, s),
            "group": 0,
            "hop": 1,
            "relation": "coauthor",
        }
    for rel in _person_relations_all(session, row_id, "advised_by"):
        other = int(rel.to_person_id if rel.from_person_id == row_id else rel.from_person_id)
        entry = alters.setdefault(
            other,
            {"personId": encode("person", other), "paperCount": 0, "importance": 0.0,
             "group": 0, "hop": 1, "relation": None},
        )
        entry["relation"] = "advisor" if rel.from_person_id == row_id else "advisee"
        entry["importance"] = max(entry["importance"], 0.55)

    ordered = sorted(alters.values(), key=lambda a: -a["importance"])[:PERSPECTIVE_ALTER_LIMIT]
    alter_ids = [int(decode(a["personId"])[1]) for a in ordered]
    groups = _alter_groups(session, alter_ids)
    for a in ordered:
        pid = int(decode(a["personId"])[1])
        a["group"] = groups.get(pid, 0)
        brief = _person_brief(session, pid, on)
        if brief:
            person = brief["person"]
            a["label"] = _full_name(person.firstname, person.middlename, person.lastname)
            a["institution"] = brief["institution"]
            a["rank"] = brief["rank"]
        else:
            a["label"] = "?"
    max_paper = max((a["paperCount"] or 0) for a in ordered) if ordered else 0
    return {
        "focusId": personId,
        "alterCount": len(ordered),
        "maxPaperCount": max_paper,
        "alters": ordered,
    }
```

- [ ] **Step 3: TS query + types**

Create `src/api/perspective.ts`:

```ts
import { gql } from "@apollo/client";

export const PERSPECTIVE = gql`
  query Perspective($personId: ID!) {
    perspective(personId: $personId) {
      focusId
      alterCount
      maxPaperCount
      alters {
        personId
        label
        institution
        rank
        importance
        group
        hop
        paperCount
        relation
      }
    }
  }
`;

export interface PerspectiveAlter {
  personId: string;
  label: string;
  institution: string | null;
  rank: string | null;
  importance: number;
  group: number;
  hop: number;
  paperCount: number | null;
  relation: string | null;
}

export interface PerspectiveData {
  perspective: {
    focusId: string;
    alterCount: number;
    maxPaperCount: number;
    alters: PerspectiveAlter[];
  };
}
```

- [ ] **Step 4: Pure layout helper**

Create `src/lib/perspectiveLayout.ts`:

```ts
/**
 * Pure polar-layout helpers for the perspective (ego) view.
 * r = asymmetric importance; theta = alter-alter community wedges.
 * No React — unit-testable.
 */

export interface PolarPoint {
  r: number; // 0 = focus, 1 = outer rim
  theta: number; // radians, 0 = +x axis
}

/** Importance [0,1] -> radius [inner, rim] with linear mapping. */
export function importanceToRadius(importance: number, inner = 0.08, rim = 1): number {
  return inner + (rim - inner) * (1 - Math.min(1, Math.max(0, importance)));
}

/**
 * Assign theta to each alter index: groups sorted by size (desc) get wedges
 * proportional to their member count; within a group, members are spread
 * with deterministic golden-angle spacing (no random jitter).
 */
export function groupThetas(
  groupOf: (index: number) => number,
  count: number,
): number[] {
  const byGroup = new Map<number, number[]>();
  for (let i = 0; i < count; i++) {
    const g = groupOf(i);
    const arr = byGroup.get(g) ?? [];
    arr.push(i);
    byGroup.set(g, arr);
  }
  const groups = [...byGroup.entries()].sort((a, b) => b[1].length - a[1].length);
  const theta = new Array<number>(count).fill(0);
  const GOLDEN = (Math.sqrt(5) - 1) / 2;
  let start = 0;
  for (const [, members] of groups) {
    const wedge = (members.length / count) * Math.PI * 2;
    members.forEach((idx, k) => {
      const frac = members.length === 1 ? 0.5 : (k + 0.5) / members.length;
      // golden-angle wobble inside the wedge keeps close pairs from stacking
      const wobble = (k * GOLDEN * 0.3 - 0.15) * (wedge / members.length);
      theta[idx] = start + frac * wedge + wobble;
    });
    start += wedge;
  }
  return theta;
}

export function polarToCartesian(
  center: { x: number; y: number },
  p: PolarPoint,
  radiusPx: number,
): { x: number; y: number } {
  return {
    x: center.x + p.r * radiusPx * Math.cos(p.theta),
    y: center.y + p.r * radiusPx * Math.sin(p.theta),
  };
}
```

Create `src/lib/perspectiveLayout.test.ts`:

```ts
import { describe, expect, it } from "vitest";
import {
  groupThetas,
  importanceToRadius,
  polarToCartesian,
} from "./perspectiveLayout";

describe("importanceToRadius", () => {
  it("maps top importance to inner ring and zero to the rim", () => {
    expect(importanceToRadius(1)).toBeCloseTo(0.08);
    expect(importanceToRadius(0)).toBeCloseTo(1);
    expect(importanceToRadius(0.5)).toBeCloseTo(0.54);
  });
});

describe("groupThetas", () => {
  it("keeps group members in contiguous wedges sorted by size", () => {
    // alters 0..4 in group A, 5..6 in group B -> A gets the bigger wedge
    const theta = groupThetas((i) => (i < 5 ? 0 : 1), 7);
    const ta = theta.slice(0, 5).sort((x, y) => x - y);
    const tb = theta.slice(5).sort((x, y) => x - y);
    expect(ta[ta.length - 1] - ta[0]).toBeLessThan(Math.PI); // wedge < half circle
    expect(tb[tb.length - 1] - tb[0]).toBeLessThan(Math.PI);
    expect(ta[0]).toBeGreaterThanOrEqual(0);
    expect(tb[0]).toBeGreaterThanOrEqual(0);
    expect(theta).toHaveLength(7);
  });

  it("is deterministic", () => {
    const a = groupThetas((i) => i % 2, 10);
    const b = groupThetas((i) => i % 2, 10);
    expect(a).toEqual(b);
  });
});

describe("polarToCartesian", () => {
  it("places r=0 at the center", () => {
    const p = polarToCartesian({ x: 10, y: 20 }, { r: 0, theta: 1.3 }, 100);
    expect(p.x).toBeCloseTo(10);
    expect(p.y).toBeCloseTo(20);
  });
});
```

- [ ] **Step 5: Component integration**

In `PeopleScatter.tsx`:

Add state + query (next to the existing focus handling):

```ts
const [perspectiveMode, setPerspectiveMode] = useState(false);
const { data: perspData } = useQuery<PerspectiveData>(PERSPECTIVE, {
  variables: { personId: personFocus ?? "" },
  skip: personFocus == null || !perspectiveMode,
  fetchPolicy: "cache-first",
});
const alters = perspData?.perspective.alters ?? [];
```

Header (personalGraph controls): add a toggle button:

```tsx
{personalGraph && (
  <button
    type="button"
    className={`people-scatter__perspective${perspectiveMode ? " is-active" : ""}`}
    onClick={() => setPerspectiveMode((v) => !v)}
  >
    {perspectiveMode ? "Exit perspective" : "Perspective"}
  </button>
)}
```

Render: when `perspectiveMode && personFocus`, replace the landscape `<g transform={worldTransform}>` content with a polar ego view (still inside the same SVG):

```tsx
{perspectiveMode && personFocus ? (
  <g transform={worldTransform}>
    {(() => {
      const cx = 0;
      const cy = 0;
      const radiusPx = Math.min(size.w, size.h) * 0.38;
      const thetas = groupThetas((i) => alters[i]?.group ?? 0, alters.length);
      const focusPoint = pointById.get(personFocus);
      const focusLabel = focusPoint?.label ?? "You";
      return (
        <>
          <circle cx={cx} cy={cy} r={radiusPx} fill="none" stroke="#d4d4d8" strokeDasharray="4 4" />
          {alters.map((alt, i) => {
            const r = importanceToRadius(alt.importance);
            const pt = polarToCartesian({ x: cx, y: cy }, { r, theta: thetas[i] }, radiusPx);
            const size = 3 + Math.min(9, (alt.paperCount ?? 0) / Math.max(1, perspData!.perspective.maxPaperCount) * 8);
            return (
              <g key={alt.personId}>
                <line
                  x1={cx} y1={cy} x2={pt.x} y2={pt.y}
                  stroke={alt.hop === 2 ? "#a1a1aa" : "#6366f1"}
                  strokeWidth={0.5 * (1 + 4 * alt.importance)}
                  opacity={alt.hop === 2 ? 0.3 : 0.35 + 0.5 * alt.importance}
                  strokeLinecap="round"
                />
                <circle
                  cx={pt.x} cy={pt.y} r={size * invScale}
                  fill={clusterColorSlot(alt.group % 12)}
                  opacity={alt.hop === 2 ? 0.4 : 0.9}
                  data-scatter-point
                  onClick={(e) => {
                    e.stopPropagation();
                    onFocus(alt.personId);
                  }}
                >
                  <title>{`${alt.label} — ${alt.paperCount ?? 0} papers together`}</title>
                </circle>
              </g>
            );
          })}
          <circle cx={cx} cy={cy} r={9 * invScale} fill={FOCUS_COLOR} />
          <text x={cx} y={cy - 14 * invScale} textAnchor="middle" className="people-scatter__perspective-focus">
            {shortLabel(focusLabel, 20)}
          </text>
        </>
      );
    })()}
  </g>
) : (
  <g transform={worldTransform}> {/* existing landscape content unchanged */} </g>
)}
```

Import `PERSPECTIVE`, `PerspectiveData` from `../api/perspective` and `groupThetas`, `importanceToRadius`, `polarToCartesian` from `../lib/perspectiveLayout`. Entering perspective must NOT re-layout the landscape (landscape coords untouched by design); exiting restores the previous camera state.

- [ ] **Step 6: Tests + verification**

Run: `npm test` — all tests pass (new + existing).
Run: `npm run dev`, search a person, click them (landscape focus unchanged), click "Perspective" — verify: top collaborator closest to center, alter groups in wedges, advisors at importance ≥ 0.55, hop-2 alters dimmed at the rim, click an alter → landscape refocuses (or perspective re-centers on the new person via `onFocus` — verify behavior is sane and note it).

- [ ] **Step 7: Commit**

```bash
git add api/graphql/schema.graphql api/graphql/resolvers.py src/api/perspective.ts src/lib/perspectiveLayout.ts src/lib/perspectiveLayout.test.ts src/components/PeopleScatter.tsx
git commit -m "feat(web): ego-centric perspective mode with asymmetric importance"
```

---

## Self-Review Notes

- Spec coverage: topics backfill → Task 3; two partitions → Tasks 4/6; two-level layouts + weighted cluster edges → Tasks 5/8; metrics replacement + gold set → Task 7; schema/API → Task 9; frontend dual view/edges/hulls → Tasks 10/11; pilot tuning + docs → Task 12; scale-to-thousands → precomputed runs + `view` column (Task 8) + capped cluster counts (`cluster_targets`) + top-N edge rendering (Task 11); OpenAlex-fallback to concepts is a data-tier concern — Task 3's `person_topics` emptiness is handled by `assign_topic_clusters`'s Unknown cluster and topic-view fallback to `classical_mds` on all-zero profiles (degenerate but non-crashing).
- Old `similarityGroup` is dropped from the API and frontend (replaced by `clusterId`/`clusterLabel`); `personCoauthorTies`, `expand`, `search`, institution color, focus mode untouched.
- Type consistency: `write_run` in Task 8 keys views by `"network"`/`"topic"` matching the `view` column and GraphQL values; `build_topic_view` returns 5-tuple including `names` (the text in Task 8 shows both the 4-tuple version and the correction — implement the corrected 5-tuple version).
- Placeholders: none; every step has concrete code or an exact command with expected output.
