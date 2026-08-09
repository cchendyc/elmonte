# Researcher knowledge graph

**Evidence-oriented**, **time-aware** researcher–institution graph with
search/disambiguation, person timeline + affiliation graph (synced by year),
institution pages, and exploration mode.  Monorepo: `frontend/` (React 19 +
Vite) and `backend/` (FastAPI + Ariadne GraphQL + PostgreSQL).

## Run locally

Prerequisites: Node ≥ 22, Python 3.13 (deps: `pip install -r
backend/requirements.txt`), and a Postgres — the project runs on **Neon**
(copy `.env.example` to `.env` and paste your pooled URL as `DATABASE_URL`,
the direct URL as `DIRECT_URL`).

```bash
npm install            # root orchestrator + frontend deps
npm run dev            # starts Vite (5173) + uvicorn (8000) together
```

`npm run dev:web` / `npm run dev:api` start either half.  The Vite dev server
proxies `/api/*` to `:8000`.

## Repository layout

```
frontend/   React app (src, vite/tsconfig, public, package.json)
backend/    FastAPI app (api/), SQLAlchemy models + alembic (db/),
            data pipelines (scripts/), pytest suite (tests/)
data/       ingested snapshot cache + reports (repo-root, gitignored raw/)
```

Backend layers (all under `backend/api/`):

| Layer | Path | Role |
|---|---|---|
| GraphQL resolvers | `graphql/resolvers/*.py` | field wiring, `QueryType` registry |
| Repositories | `repositories/{people,orgs,projection}.py` | SQL data access |
| Services | `services/{person,graph,impact,names,orgs}.py` | business logic |
| App | `main.py`, `graphql/app.py`, `deps.py` | FastAPI + Ariadne plumbing |

## Data

One canonical dataset lives in the database (Neon):

- **Legacy seed** (original repo data): `cd backend && python3 -m
  scripts.db.restore_legacy_seed` loads `db/seed.sql` — 9 universities
  (Toronto, MIT, Stanford, UCLA, Berkeley, Tsinghua, Peking, Chicago, ETH
  Zurich), 12 people, concepts, awards, grants, evidence — converting the
  legacy text ids, deriving topics/person_topics, refreshing the
  materialized views, and syncing identity sequences.
- **Real OpenAlex data** (CC0): the full E2E backfill, run in order:

```bash
cd backend
python3 -m scripts.backfill.taxonomy               # topics taxonomy (~4.5k)
python3 -m scripts.backfill.taxonomy --concepts    # concepts tree (~65k, flat — see below)
python3 -m scripts.backfill.publications --all-universities --refresh
python3 -m scripts.backfill.topics                 # per-work topics -> person_topics
python3 -m scripts.backfill.concepts --all-universities   # publication/person concepts
python3 -m scripts.embed.build_atlas --view both   # rebuild the atlas
```

Data pipelines connect through `DATABASE_URL`; for migrations and psql the
scripts prefer `DIRECT_URL` (Neon's direct endpoint, no PgBouncer).  The
pytest suite runs against `TEST_DATABASE_URL` (a separate `neondb_test`
database), never the app database.

**Concept hierarchy note**: OpenAlex no longer returns the concept parent
tree (the `ancestors` field is empty in current responses and there is no
`parent_id`), so `concepts` is stored flat with its `level`; `parent_id`
stays unused.  The three-level *topics* hierarchy (domain → field →
subfield → topic) is unaffected and powers the atlas topic view.

## Data sources & compliance

- **OpenAlex** (`https://api.openalex.org`): taxonomy and publication
  metadata are bulk-fetched through the public API.  OpenAlex data is
  dedicated to the public domain (**CC0**), so storing, displaying and
  re-distributing it is unrestricted; the app shows "Publication data from
  OpenAlex" attribution as courtesy.
- **Polite pool**: the client sends a contact User-Agent (set
  `OPENALEX_CONTACT_EMAIL` in `.env`), self-limits to ~3 requests/second on
  the keyless tier (10/s with `OPENALEX_API_KEY`), and retries with backoff
  on 429/network errors.
- **Minimization**: only public bibliographic metadata is stored — no full
  texts, no personal contact data.  People come from the school directory
  (this repo's own database); OpenAlex only enriches their public works.

## Atlas (people map) pipeline

The home scatter map is an offline two-view atlas:

1. `scripts/backfill/topics.py` — OpenAlex topics for each publication,
   aggregated into `person_topics` (topic id, score, works_count).
2. `scripts/embed/build_atlas.py` — builds both views in one run:
   - **network**: association-strength normalized coauthor graph ->
     disparity-filter backbone -> Leiden communities (resolution swept to a
     readable cluster count) -> linlog cluster layout + intra-cluster springs;
     bridge nodes are placed at the weighted barycenter of their neighboring
     clusters (nodes tied to 1-2 foreign groups sit between the hulls;
     multi-group nodes stay in their primary cluster — the weighted cluster
     edges carry their relations).
   - **topic**: TF-IDF topic profiles -> dominant OpenAlex field clusters
     (oversized fields refined by Ward) -> MDS cluster layout on centroid
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

### Tuning guide

The backbone ladder in `build_network_view` evaluates all disparity-filter
alpha levels (0.05, 0.1, 0.2, 0.5) plus a kNN fallback, picks the one whose
cluster count lands in the target range with the best `separation_ratio`, and
records the winner in `embedding_runs.notes`.  Other knobs in `build_atlas.py`:

| Constant | Effect |
|---|---|
| `BACKBONE_ALPHA` | Starting disparity-filter significance (0.03-0.1) |
| `FOOTPRINT_A`, `FOOTPRINT_B` | Cluster footprint = A + B*sqrt(members); smaller -> tighter clusters, higher sep |
| `cluster_targets(n)` | Readable cluster count range (~1 per 40 people, 8-30) |
| `members_per_cluster`, `max_field_members` | Topic-view Ward refinement granularity (40-60, 60-120) |

Run `cd backend && python3 -m scripts.embed.build_atlas --view both --dry`
to see metrics without writing to the database.  On the demo dataset (60
people), topic-view
strong-edge fidelity is inherently ~0.68 (coauthor ties cross topic boundaries
by design); the network view hits all targets (purity >= 0.7, sep >= 2.5,
fidelity <= 0.15, overlap <= 0.05).

### Suggested clicks

| Action | What to see |
|--------|-------------|
| Search `Wei Zhang` | Two disambiguation cards (ORCID, field, institution) |
| **Alice Chen** profile | Year slider, dotted former lines, disputed/unverified edges, timeline ↔ graph highlight |
| **Jennifer Doudna** | Nobel Prize as **person attribute** (not a graph node); open explore for company `founded` |
| Search `Berkeley` | Institution card → affiliated people |
| **Find the connection** on home | Alice → Patricia Lang advisor chain (demo) |
| `/explore/person-jennifer` | Click nodes to expand; filter relationship types |

> Personas and orgs above (Wei Zhang, Alice Chen, Jennifer Doudna, …) are
> demo dataset fixtures — swap in your own directory's people.

## Product principles (encoded in types/README)

- **Evidence-first edges** — relationship type, dates, sources, verification status
- **Node vs attribute** — Nobel/awards on `person.awards[]`, not movable nodes
- **Temporal affiliations** — solid / dotted / dashed / disputed styles + year overlap rule
- **Governance (stubs)** — claim, suggest edit (source URL), report edge, merge, split

## Routes

- `/` — search people & institutions, find-connection demo
- `/person/:id` — timeline modes, year controls, graph + evidence popover on edge tap
- `/institution/:id` — affiliated people in seed
- `/explore/:nodeId` — expand-on-click graph (session layout only)

## Year overlap rule

`start <= endOfYear(Y)` and (`end` is null or `end >= startOfYear(Y)`).

## PostgreSQL + FastAPI (active)

The application runs against a PostgreSQL database (Neon) with a FastAPI
backend. Schema lives in [`backend/db/schema.sql`](backend/db/schema.sql),
mirrored by SQLAlchemy models in [`backend/db/models/`](backend/db/models);
migrations are Alembic revisions in `backend/db/migrations/`. The frontend
reads the GraphQL API via the client layer in `frontend/src/api/`
(`projection.ts`, `perspective.ts`, `queries.ts`, `coauthorTies.ts`).

| Prototype | Tables |
|-----------|--------|
| `Person` | `people`, `person_aliases` |
| `Institution`, `Company` | `organizations` (one supertype, discriminated by `kind`) |
| Institution hierarchy | `org_relationships` (temporal), projected into `org_tree_current` |
| `Affiliation`, `FoundedRelationship` | `person_affiliations` + `affiliation_org_assignments` |
| `PersonRelationship` | `person_relationships` |
| Fields on person | `concepts` + `person_concepts` |
| Awards on person | `awards` + `person_awards` |
| `sources` on every edge | `evidence` + `source_snapshots` |
| Funding | `grants`, `grant_participants` |

Open vocabularies (fields, awards) are lookup tables, so adding a value is an
`INSERT`. Closed vocabularies the UI branches on (`verification_status`,
`date_precision`, `org_kind`, ...) are Postgres enums. There is no JSONB.

## Risks

- Graph clutter at scale → expand caps and filters in this demo only
- Duplicate identities → disambiguation + merge stub
- Unsourced/defamatory edges → evidence model; sample disputed affiliation
- Node positions → not canonical data (per-user/session layout in production)

Standalone project (not part of Gradgate).
