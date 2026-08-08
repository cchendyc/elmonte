# Researcher knowledge graph (local preview)

Frontend-only prototype: **evidence-oriented**, **time-aware** researcher–institution graph with search/disambiguation, person timeline + affiliation graph (synced by year), institution pages, and exploration mode.

## Run locally

```bash
cd ~/elmonte
npm install
npm run dev
```

Open the URL Vite prints (usually `http://localhost:5173`).

### Suggested clicks

| Action | What to see |
|--------|-------------|
| Search `Wei Zhang` | Two disambiguation cards (ORCID, field, institution) |
| **Alice Chen** profile | Year slider, dotted former lines, disputed/unverified edges, timeline ↔ graph highlight |
| **Jennifer Doudna** | Nobel Prize as **person attribute** (not a graph node); open explore for company `founded` |
| Search `Berkeley` | Institution card → affiliated people |
| **Find the connection** on home | Alice → Patricia Lang advisor chain (demo) |
| `/explore/person-jennifer` | Click nodes to expand; filter relationship types |

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

## Future PostgreSQL + FastAPI

Schema lives in [`db/schema.sql`](db/schema.sql), mirrored by SQLAlchemy models in
[`db/models/`](db/models). The frontend still reads the TypeScript fixtures; the
database is not wired up yet.

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

Swap [`src/data/seed.ts`](src/data/seed.ts) for API client; keep [`src/lib/temporal.ts`](src/lib/temporal.ts), [`src/lib/graphBuilder.ts`](src/lib/graphBuilder.ts), [`src/lib/expandGraph.ts`](src/lib/expandGraph.ts).

## Risks

- Graph clutter at scale → expand caps and filters in this demo only
- Duplicate identities → disambiguation + merge stub
- Unsourced/defamatory edges → evidence model; sample disputed affiliation
- Node positions → not canonical data (per-user/session layout in production)

Standalone project (not part of Gradgate).
