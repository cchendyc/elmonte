-- El Monte schema (PostgreSQL 14+, target: Neon)
--
-- Design notes
--   * `organizations` is a single supertype for universities, departments, labs,
--     companies, funders and publishers. New organization types are new `org_kind`
--     values, not new tables, so adding industry or funding data does not require
--     migrating anything that already exists.
--   * The org hierarchy lives ONLY in `org_relationships` (temporal edges).
--     `org_tree_current` materializes the as-of-today tree with ancestor paths and
--     subtree rollups, which is what the chart renders.
--   * No JSONB. Open vocabularies (research fields, awards) are lookup tables so
--     adding a value is an INSERT; closed vocabularies the application branches on
--     are enums so the database rejects values the UI cannot render.
--   * `evidence` uses an exclusive arc: one nullable subject id per row with
--     exactly one set. Supporting a new fact type later is ADD COLUMN plus a
--     widened CHECK.
--   * No foreign keys. Internal row ids are BIGINT; reference columns store the
--     same type without DB-level enforcement so ingest and merges stay flexible.
--     TEXT is reserved for actual strings (names, URLs, external ids).
--
-- This file is a deterministic rebuild: it drops the objects it owns and recreates
-- them. It is not an incremental migration.

-- ---------------------------------------------------------------------------
-- Extensions
-- ---------------------------------------------------------------------------

CREATE EXTENSION IF NOT EXISTS btree_gist;  -- scalar + range exclusion constraints
CREATE EXTENSION IF NOT EXISTS pg_trgm;     -- fuzzy name and alias search

-- ---------------------------------------------------------------------------
-- Teardown
-- ---------------------------------------------------------------------------

DROP MATERIALIZED VIEW IF EXISTS
  org_current_roster,
  person_coauthor_edges,
  person_anchor,
  org_tree_current
CASCADE;

DROP TABLE IF EXISTS
  evidence,
  external_identifiers,
  source_snapshots,
  grant_participants,
  grants,
  publication_citations,
  publication_author_affiliations,
  publication_authors,
  publication_concepts,
  publications,
  person_relationships,
  affiliation_org_assignments,
  person_affiliations,
  person_awards,
  awards,
  org_relationships,
  organizations,
  person_concepts,
  concepts,
  person_aliases,
  people,
  -- superseded by the tables above
  org_units,
  org_unit_relationships,
  companies,
  person_positions,
  position_org_assignments,
  founded_relationships
CASCADE;

DROP FUNCTION IF EXISTS set_updated_at() CASCADE;

DROP TYPE IF EXISTS
  verification_status,
  claimed_status,
  org_kind,
  org_relationship_type,
  affiliation_kind,
  position_rank,
  assignment_type,
  person_relationship_type,
  grant_role,
  identifier_provider,
  source_kind
CASCADE;

-- ---------------------------------------------------------------------------
-- Closed vocabularies
--
-- These are the sets the application switches on. Values are ordered by
-- declaration, which is also their ORDER BY order.
-- ---------------------------------------------------------------------------

CREATE TYPE verification_status AS ENUM ('verified', 'unverified', 'disputed');

CREATE TYPE claimed_status AS ENUM ('unclaimed', 'pending', 'verified');

CREATE TYPE org_kind AS ENUM (
  'university', 'school', 'department', 'lab', 'institute',
  'company', 'funder', 'nonprofit', 'government', 'consortium', 'publisher'
);

CREATE TYPE org_relationship_type AS ENUM ('primary', 'secondary');

-- The nature of the tie only. Precedence is `is_primary` and seniority is
-- `position_rank`; keeping the three axes apart is what lets a visiting PhD
-- student be expressed without inventing a combined value for it.
CREATE TYPE affiliation_kind AS ENUM (
  'employment', 'education', 'visiting', 'founding', 'governance', 'honorary'
);

-- Normalized seniority, declared junior to senior. `person_affiliations.title`
-- keeps the verbatim string from the source ('Chargé de Recherche', 'Reader',
-- 'Principal Member of Technical Staff'); this is the queryable form of it.
--
-- Normalizing rank is what makes advising inferable: a phd_student and a
-- full_professor co-present in one lab is an advising edge you can derive
-- rather than hand-enter.
CREATE TYPE position_rank AS ENUM (
  'undergraduate', 'masters_student', 'phd_student', 'visiting_student',
  'postdoc', 'research_fellow',
  'technician', 'engineer', 'research_scientist', 'staff_scientist',
  'lecturer', 'adjunct_professor', 'assistant_professor', 'associate_professor',
  'full_professor', 'emeritus_professor',
  'group_leader', 'principal_investigator', 'department_head', 'dean',
  'executive', 'board_member'
);

CREATE TYPE assignment_type AS ENUM ('chart_anchor', 'secondary');

CREATE TYPE person_relationship_type AS ENUM ('advised_by', 'collaborated_with');

CREATE TYPE grant_role AS ENUM ('principal_investigator', 'co_investigator', 'recipient');

CREATE TYPE identifier_provider AS ENUM (
  'openalex', 'orcid', 'doi', 'ror', 'wikidata', 'grid', 'crossref_funder', 'official_url'
);

CREATE TYPE source_kind AS ENUM (
  'official_roster', 'official_profile', 'openalex', 'crossref', 'ror', 'news', 'manual'
);

-- ---------------------------------------------------------------------------
-- Shared triggers
-- ---------------------------------------------------------------------------

CREATE FUNCTION set_updated_at() RETURNS TRIGGER
LANGUAGE plpgsql AS $$
BEGIN
  NEW.updated_at := now();
  RETURN NEW;
END;
$$;

-- ---------------------------------------------------------------------------
-- People
--
-- ORCID lives in `external_identifiers`, not here, so identity has one home.
-- ---------------------------------------------------------------------------

CREATE TABLE people (
  id             BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  firstname      TEXT NOT NULL,
  middlename     TEXT,
  lastname       TEXT NOT NULL,
  biography      TEXT,
  homepage_url   TEXT,
  claimed_status claimed_status NOT NULL DEFAULT 'unclaimed',
  created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT people_firstname_not_blank CHECK (btrim(firstname) <> ''),
  CONSTRAINT people_lastname_not_blank CHECK (btrim(lastname) <> ''),
  CONSTRAINT people_homepage_url_scheme
    CHECK (homepage_url IS NULL OR homepage_url ~* '^https?://')
);

CREATE TRIGGER people_set_updated_at BEFORE UPDATE ON people
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_people_lastname ON people (lastname);
CREATE INDEX idx_people_firstname_trgm ON people USING gin (firstname gin_trgm_ops);
CREATE INDEX idx_people_lastname_trgm ON people USING gin (lastname gin_trgm_ops);

CREATE TABLE person_aliases (
  person_id BIGINT NOT NULL,
  alias     TEXT NOT NULL,
  PRIMARY KEY (person_id, alias),
  CONSTRAINT person_aliases_not_blank CHECK (btrim(alias) <> '')
);

CREATE INDEX idx_person_aliases_trgm ON person_aliases USING gin (alias gin_trgm_ops);

-- ---------------------------------------------------------------------------
-- Concepts (research fields)
--
-- Replaces people.fields JSONB and publications.topic. Shared by people and
-- publications so topical similarity is a join rather than string matching.
-- ---------------------------------------------------------------------------

CREATE TABLE concepts (
  id           BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  display_name TEXT NOT NULL,
  parent_id    BIGINT,
  level        SMALLINT NOT NULL DEFAULT 0,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT concepts_no_self_parent CHECK (parent_id IS NULL OR parent_id <> id),
  CONSTRAINT concepts_level_non_negative CHECK (level >= 0)
);

CREATE UNIQUE INDEX uq_concepts_display_name ON concepts (display_name);
CREATE INDEX idx_concepts_parent ON concepts (parent_id);
CREATE INDEX idx_concepts_name_trgm ON concepts USING gin (display_name gin_trgm_ops);

CREATE TABLE person_concepts (
  person_id  BIGINT NOT NULL,
  concept_id BIGINT NOT NULL,
  score      REAL,
  rank       SMALLINT,
  PRIMARY KEY (person_id, concept_id),
  CONSTRAINT person_concepts_score_range CHECK (score IS NULL OR (score >= 0 AND score <= 1)),
  CONSTRAINT person_concepts_rank_positive CHECK (rank IS NULL OR rank > 0)
);

CREATE INDEX idx_person_concepts_concept ON person_concepts (concept_id);

-- ---------------------------------------------------------------------------
-- Organizations
--
-- One table for every organization type. `kind` discriminates; kind-specific
-- columns belong in 1:1 detail tables so this stays narrow.
-- ---------------------------------------------------------------------------

CREATE TABLE organizations (
  id              BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  name            TEXT NOT NULL,
  short_name      TEXT,
  kind            org_kind NOT NULL,
  country         TEXT,
  homepage_url    TEXT,
  description     TEXT,
  is_context_only BOOLEAN NOT NULL DEFAULT FALSE,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT organizations_name_not_blank CHECK (btrim(name) <> ''),
  CONSTRAINT organizations_country_iso CHECK (country IS NULL OR country ~ '^[A-Z]{2}$')
);

CREATE TRIGGER organizations_set_updated_at BEFORE UPDATE ON organizations
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_organizations_kind ON organizations (kind);
CREATE INDEX idx_organizations_name_trgm ON organizations USING gin (name gin_trgm_ops);

-- Temporal parent/child edges. This is the only source of truth for hierarchy.
-- The exclusion constraint guarantees a unit has at most one primary parent at
-- any instant, which is what keeps the chart a tree rather than a DAG.
CREATE TABLE org_relationships (
  id                  BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  child_org_id        BIGINT NOT NULL,
  parent_org_id       BIGINT NOT NULL,
  relationship_type   org_relationship_type NOT NULL DEFAULT 'primary',
  starts_at           TIMESTAMPTZ,
  ends_at             TIMESTAMPTZ,
  validity            daterange GENERATED ALWAYS AS (daterange((starts_at AT TIME ZONE 'UTC')::date, (ends_at AT TIME ZONE 'UTC')::date, '[)')) STORED,
  verification_status verification_status NOT NULL DEFAULT 'unverified',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT org_relationships_no_self CHECK (child_org_id <> parent_org_id),
  CONSTRAINT org_relationships_date_order
    CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at),
  CONSTRAINT org_relationships_one_primary_parent
    EXCLUDE USING gist (child_org_id WITH =, validity WITH &&)
    WHERE (relationship_type = 'primary')
);

CREATE TRIGGER org_relationships_set_updated_at BEFORE UPDATE ON org_relationships
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_org_relationships_parent ON org_relationships (parent_org_id);
CREATE INDEX idx_org_relationships_child ON org_relationships (child_org_id);
CREATE INDEX idx_org_relationships_validity ON org_relationships USING gist (validity);

-- ---------------------------------------------------------------------------
-- Awards
--
-- The prize and the conferral are separate so co-recipients are queryable.
-- ---------------------------------------------------------------------------

CREATE TABLE awards (
  id              BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  name            TEXT NOT NULL,
  awarding_org_id BIGINT,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_awards_name ON awards (name);
CREATE INDEX idx_awards_awarding_org ON awards (awarding_org_id);

CREATE TABLE person_awards (
  id                  BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  person_id           BIGINT NOT NULL,
  award_id            BIGINT NOT NULL,
  awarded_at          TIMESTAMPTZ,
  verification_status verification_status NOT NULL DEFAULT 'unverified',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX uq_person_awards_person_award_date
  ON person_awards (person_id, award_id, awarded_at);
CREATE INDEX idx_person_awards_person ON person_awards (person_id);
CREATE INDEX idx_person_awards_award ON person_awards (award_id);

-- ---------------------------------------------------------------------------
-- Affiliations (person <-> organization)
--
-- Replaces person_positions and founded_relationships. Founding a company or
-- sitting on a board is an affiliation_kind, not a separate table, so new
-- person/organization relationships do not add tables.
--
-- Three independent axes, deliberately not collapsed into one column:
--   affiliation_kind  what kind of tie it is
--   position_rank     normalized seniority (title keeps the verbatim string)
--   is_primary        which tie is the person's main one
-- ---------------------------------------------------------------------------

CREATE TABLE person_affiliations (
  id                  BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  person_id           BIGINT NOT NULL,
  title               TEXT,
  affiliation_kind    affiliation_kind NOT NULL,
  position_rank       position_rank,
  is_primary          BOOLEAN NOT NULL DEFAULT FALSE,
  starts_at           TIMESTAMPTZ,
  ends_at             TIMESTAMPTZ,
  validity            daterange GENERATED ALWAYS AS (daterange((starts_at AT TIME ZONE 'UTC')::date, (ends_at AT TIME ZONE 'UTC')::date, '[)')) STORED,
  verification_status verification_status NOT NULL DEFAULT 'unverified',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT person_affiliations_date_order
    CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at),
  -- One main affiliation at any instant. Concurrent secondary ties (a founding,
  -- an adjunct appointment, a board seat) are unrestricted.
  CONSTRAINT person_affiliations_one_primary
    EXCLUDE USING gist (person_id WITH =, validity WITH &&)
    WHERE (is_primary)
);

CREATE TRIGGER person_affiliations_set_updated_at BEFORE UPDATE ON person_affiliations
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_person_affiliations_person ON person_affiliations (person_id);
CREATE INDEX idx_person_affiliations_validity ON person_affiliations USING gist (validity);
CREATE INDEX idx_person_affiliations_current ON person_affiliations (person_id)
  WHERE ends_at IS NULL;
CREATE INDEX idx_person_affiliations_rank ON person_affiliations (position_rank)
  WHERE position_rank IS NOT NULL;

-- A single appointment can span several units (joint appointments); exactly one
-- of them is the chart anchor that decides where the person hangs in the tree.
CREATE TABLE affiliation_org_assignments (
  affiliation_id  BIGINT NOT NULL,
  organization_id BIGINT NOT NULL,
  assignment_type assignment_type NOT NULL DEFAULT 'secondary',
  PRIMARY KEY (affiliation_id, organization_id)
);

CREATE UNIQUE INDEX one_chart_anchor_per_affiliation
  ON affiliation_org_assignments (affiliation_id)
  WHERE assignment_type = 'chart_anchor';

CREATE INDEX idx_affiliation_assignments_org ON affiliation_org_assignments (organization_id);

-- ---------------------------------------------------------------------------
-- Person <-> person relationships
--
-- Symmetric types are stored once in canonical id order so "shared collaborator"
-- counts cannot double-count the same pair.
-- ---------------------------------------------------------------------------

CREATE TABLE person_relationships (
  id                  BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  type                person_relationship_type NOT NULL,
  from_person_id      BIGINT NOT NULL,
  to_person_id        BIGINT NOT NULL,
  starts_at           TIMESTAMPTZ,
  ends_at             TIMESTAMPTZ,
  validity            daterange GENERATED ALWAYS AS (daterange((starts_at AT TIME ZONE 'UTC')::date, (ends_at AT TIME ZONE 'UTC')::date, '[)')) STORED,
  verification_status verification_status NOT NULL DEFAULT 'unverified',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT person_relationships_no_self CHECK (from_person_id <> to_person_id),
  CONSTRAINT person_relationships_symmetric_canonical
    CHECK (type <> 'collaborated_with' OR from_person_id < to_person_id),
  CONSTRAINT person_relationships_date_order
    CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at)
);

CREATE TRIGGER person_relationships_set_updated_at BEFORE UPDATE ON person_relationships
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE UNIQUE INDEX uq_person_relationships_edge
  ON person_relationships (type, from_person_id, to_person_id);
CREATE INDEX idx_person_relationships_from ON person_relationships (from_person_id, type);
CREATE INDEX idx_person_relationships_to ON person_relationships (to_person_id, type);

-- ---------------------------------------------------------------------------
-- Publications
-- ---------------------------------------------------------------------------

CREATE TABLE publications (
  id               BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  title            TEXT NOT NULL,
  publication_year SMALLINT NOT NULL,
  publication_date DATE,
  cited_by_count   INTEGER,
  venue_org_id     BIGINT,
  created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT publications_year_sane CHECK (publication_year BETWEEN 1500 AND 2200),
  CONSTRAINT publications_cited_by_non_negative
    CHECK (cited_by_count IS NULL OR cited_by_count >= 0)
);

CREATE TRIGGER publications_set_updated_at BEFORE UPDATE ON publications
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE INDEX idx_publications_year ON publications (publication_year);
-- Postgres does not index reference columns automatically; without this,
-- filtering publications by venue scans the full table.
CREATE INDEX idx_publications_venue ON publications (venue_org_id)
  WHERE venue_org_id IS NOT NULL;
CREATE INDEX idx_publications_title_trgm ON publications USING gin (title gin_trgm_ops);

CREATE TABLE publication_concepts (
  publication_id BIGINT NOT NULL,
  concept_id     BIGINT NOT NULL,
  score          REAL,
  PRIMARY KEY (publication_id, concept_id),
  CONSTRAINT publication_concepts_score_range
    CHECK (score IS NULL OR (score >= 0 AND score <= 1))
);

CREATE INDEX idx_publication_concepts_concept ON publication_concepts (concept_id);

CREATE TABLE publication_authors (
  publication_id   BIGINT NOT NULL,
  person_id        BIGINT NOT NULL,
  author_position  SMALLINT NOT NULL,
  is_corresponding BOOLEAN NOT NULL DEFAULT FALSE,
  PRIMARY KEY (publication_id, person_id),
  CONSTRAINT publication_authors_position_positive CHECK (author_position > 0)
);

CREATE UNIQUE INDEX uq_publication_authors_position
  ON publication_authors (publication_id, author_position);
CREATE INDEX idx_publication_authors_person ON publication_authors (person_id);

-- Affiliation as printed on the paper, which is not necessarily the person's
-- affiliation today.
CREATE TABLE publication_author_affiliations (
  id                  BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  publication_id      BIGINT NOT NULL,
  person_id           BIGINT NOT NULL,
  organization_id     BIGINT NOT NULL,
  verification_status verification_status NOT NULL DEFAULT 'unverified'
);

CREATE UNIQUE INDEX uq_pub_author_affiliations_triple
  ON publication_author_affiliations (publication_id, person_id, organization_id);
CREATE INDEX idx_pub_author_affiliations_org ON publication_author_affiliations (organization_id);

CREATE TABLE publication_citations (
  citing_publication_id BIGINT NOT NULL,
  cited_publication_id  BIGINT NOT NULL,
  PRIMARY KEY (citing_publication_id, cited_publication_id),
  CONSTRAINT publication_citations_no_self
    CHECK (citing_publication_id <> cited_publication_id)
);

CREATE INDEX idx_publication_citations_cited ON publication_citations (cited_publication_id);

-- ---------------------------------------------------------------------------
-- Funding
--
-- Funders are organizations, so this needed no changes elsewhere to add.
-- ---------------------------------------------------------------------------

CREATE TABLE grants (
  id                  BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  title               TEXT NOT NULL,
  funder_org_id       BIGINT NOT NULL,
  award_number        TEXT,
  amount              NUMERIC(16, 2),
  currency            CHAR(3),
  starts_at           TIMESTAMPTZ,
  ends_at             TIMESTAMPTZ,
  validity            daterange GENERATED ALWAYS AS (daterange((starts_at AT TIME ZONE 'UTC')::date, (ends_at AT TIME ZONE 'UTC')::date, '[)')) STORED,
  verification_status verification_status NOT NULL DEFAULT 'unverified',
  created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT grants_amount_non_negative CHECK (amount IS NULL OR amount >= 0),
  CONSTRAINT grants_currency_iso CHECK (currency IS NULL OR currency ~ '^[A-Z]{3}$'),
  CONSTRAINT grants_date_order
    CHECK (ends_at IS NULL OR starts_at IS NULL OR ends_at >= starts_at)
);

CREATE TRIGGER grants_set_updated_at BEFORE UPDATE ON grants
  FOR EACH ROW EXECUTE FUNCTION set_updated_at();

CREATE UNIQUE INDEX uq_grants_funder_award_number
  ON grants (funder_org_id, award_number);
CREATE INDEX idx_grants_funder ON grants (funder_org_id);
CREATE INDEX idx_grants_validity ON grants USING gist (validity);

CREATE TABLE grant_participants (
  grant_id        BIGINT NOT NULL,
  person_id       BIGINT NOT NULL,
  organization_id BIGINT,
  role            grant_role NOT NULL DEFAULT 'principal_investigator',
  PRIMARY KEY (grant_id, person_id, role)
);

CREATE INDEX idx_grant_participants_person ON grant_participants (person_id);
CREATE INDEX idx_grant_participants_org ON grant_participants (organization_id);

-- ---------------------------------------------------------------------------
-- Provenance
-- ---------------------------------------------------------------------------

-- The single home for a source URL. Everything that needs to say "we got this
-- from somewhere" points here instead of carrying its own copy of the string.
CREATE TABLE source_snapshots (
  id           BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  source_url   TEXT NOT NULL,
  source_kind  source_kind NOT NULL,
  fetched_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  content_hash TEXT NOT NULL,
  local_path   TEXT,
  http_status  INTEGER
);

CREATE UNIQUE INDEX uq_source_snapshots_url_hash
  ON source_snapshots (source_url, content_hash);
CREATE INDEX idx_source_snapshots_url ON source_snapshots (source_url);

-- Replaces the `sources` JSONB column that was repeated on four tables.
-- Exactly one subject column is set; adding a new evidenced fact type is
-- ADD COLUMN plus widening the CHECK.
CREATE TABLE evidence (
  id                        BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  label                     TEXT,
  -- Required: citing a page we never fetched is not evidence, and the URL
  -- lives on the snapshot so it is stored once rather than per citation.
  snapshot_id               BIGINT NOT NULL,
  created_at                TIMESTAMPTZ NOT NULL DEFAULT now(),

  affiliation_id            BIGINT,
  person_relationship_id    BIGINT,
  org_relationship_id       BIGINT,
  person_award_id           BIGINT,
  grant_id                  BIGINT,
  pub_author_affiliation_id BIGINT,
  person_id                 BIGINT,
  organization_id           BIGINT,

  CONSTRAINT evidence_exactly_one_subject CHECK (
    num_nonnulls(
      affiliation_id, person_relationship_id, org_relationship_id,
      person_award_id, grant_id, pub_author_affiliation_id,
      person_id, organization_id
    ) = 1
  )
);

CREATE INDEX idx_evidence_affiliation ON evidence (affiliation_id) WHERE affiliation_id IS NOT NULL;
CREATE INDEX idx_evidence_person_relationship ON evidence (person_relationship_id) WHERE person_relationship_id IS NOT NULL;
CREATE INDEX idx_evidence_org_relationship ON evidence (org_relationship_id) WHERE org_relationship_id IS NOT NULL;
CREATE INDEX idx_evidence_person_award ON evidence (person_award_id) WHERE person_award_id IS NOT NULL;
CREATE INDEX idx_evidence_grant ON evidence (grant_id) WHERE grant_id IS NOT NULL;
CREATE INDEX idx_evidence_pub_author_affiliation ON evidence (pub_author_affiliation_id) WHERE pub_author_affiliation_id IS NOT NULL;
CREATE INDEX idx_evidence_person ON evidence (person_id) WHERE person_id IS NOT NULL;
CREATE INDEX idx_evidence_organization ON evidence (organization_id) WHERE organization_id IS NOT NULL;
CREATE INDEX idx_evidence_snapshot ON evidence (snapshot_id);

-- Same exclusive-arc pattern. (provider, external_id) is the identity backbone
-- used to detect that two records are the same entity.
CREATE TABLE external_identifiers (
  id              BIGINT GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  provider        identifier_provider NOT NULL,
  external_id     TEXT NOT NULL,
  -- Where we saw it, by reference. Nullable because an identifier can be
  -- entered by hand without a fetched page behind it.
  snapshot_id     BIGINT,
  verified_at     TIMESTAMPTZ,
  created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),

  person_id       BIGINT,
  organization_id BIGINT,
  publication_id  BIGINT,
  concept_id      BIGINT,
  grant_id        BIGINT,

  CONSTRAINT external_identifiers_exactly_one_subject CHECK (
    num_nonnulls(person_id, organization_id, publication_id, concept_id, grant_id) = 1
  )
);

CREATE UNIQUE INDEX uq_external_identifiers_provider_id
  ON external_identifiers (provider, external_id);
CREATE INDEX idx_external_identifiers_snapshot
  ON external_identifiers (snapshot_id) WHERE snapshot_id IS NOT NULL;

-- One identifier per provider per entity: replaces the old people.orcid and
-- publications.doi uniqueness without a second copy of the value.
CREATE UNIQUE INDEX one_provider_id_per_person ON external_identifiers (person_id, provider) WHERE person_id IS NOT NULL;
CREATE UNIQUE INDEX one_provider_id_per_organization ON external_identifiers (organization_id, provider) WHERE organization_id IS NOT NULL;
CREATE UNIQUE INDEX one_provider_id_per_publication ON external_identifiers (publication_id, provider) WHERE publication_id IS NOT NULL;
CREATE UNIQUE INDEX one_provider_id_per_concept ON external_identifiers (concept_id, provider) WHERE concept_id IS NOT NULL;
CREATE UNIQUE INDEX one_provider_id_per_grant ON external_identifiers (grant_id, provider) WHERE grant_id IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Current tree projection
--
-- The renderer needs ancestors, depth and subtree rollups for collapsed nodes.
-- Computing those per node was O(n^2) in the client; this precomputes them.
-- `ancestor_ids` is BIGINT[] — one array slot per ancestor organization id.
--
-- Refresh after changing organizations, org_relationships, or affiliations:
--   REFRESH MATERIALIZED VIEW CONCURRENTLY org_tree_current;
-- ---------------------------------------------------------------------------

CREATE MATERIALIZED VIEW org_tree_current AS
WITH RECURSIVE current_edges AS (
  SELECT child_org_id, parent_org_id
  FROM org_relationships
  WHERE relationship_type = 'primary'
    AND validity @> CURRENT_DATE
),
roots AS (
  SELECT o.id
  FROM organizations o
  LEFT JOIN current_edges e ON e.child_org_id = o.id
  WHERE e.child_org_id IS NULL
),
walk AS (
  SELECT
    o.id                 AS organization_id,
    NULL::BIGINT         AS parent_id,
    o.id                 AS root_id,
    0                    AS depth,
    ARRAY[o.id]          AS ancestor_ids
  FROM organizations o
  JOIN roots r ON r.id = o.id

  UNION ALL

  SELECT
    e.child_org_id,
    w.organization_id,
    w.root_id,
    w.depth + 1,
    w.ancestor_ids || e.child_org_id
  FROM walk w
  JOIN current_edges e ON e.parent_org_id = w.organization_id
  WHERE NOT e.child_org_id = ANY (w.ancestor_ids)
),
direct_people AS (
  SELECT a.organization_id, count(DISTINCT pa.person_id) AS person_count
  FROM affiliation_org_assignments a
  JOIN person_affiliations pa ON pa.id = a.affiliation_id
  WHERE a.assignment_type = 'chart_anchor'
    AND pa.validity @> CURRENT_DATE
  GROUP BY a.organization_id
)
SELECT
  w.organization_id,
  w.parent_id,
  w.root_id,
  w.depth,
  w.ancestor_ids,
  o.kind,
  o.name,
  (SELECT count(*) FROM walk c WHERE c.parent_id = w.organization_id)::INT
    AS child_count,
  (SELECT count(*) FROM walk d
     WHERE w.organization_id = ANY (d.ancestor_ids)
       AND d.organization_id <> w.organization_id)::INT
    AS descendant_count,
  (SELECT count(*) FROM walk d
     JOIN organizations od ON od.id = d.organization_id
     WHERE w.organization_id = ANY (d.ancestor_ids)
       AND d.organization_id <> w.organization_id
       AND od.kind = 'lab')::INT
    AS descendant_lab_count,
  COALESCE((SELECT sum(dp.person_count) FROM walk d
     JOIN direct_people dp ON dp.organization_id = d.organization_id
     WHERE w.organization_id = ANY (d.ancestor_ids)), 0)::INT
    AS subtree_person_count
FROM walk w
JOIN organizations o ON o.id = w.organization_id;

CREATE UNIQUE INDEX org_tree_current_organization_id ON org_tree_current (organization_id);
CREATE INDEX org_tree_current_parent ON org_tree_current (parent_id);
CREATE INDEX org_tree_current_root_depth ON org_tree_current (root_id, depth);
CREATE INDEX org_tree_current_ancestors ON org_tree_current USING gin (ancestor_ids);

-- ---------------------------------------------------------------------------
-- Hop views
--
-- Each hop of the click-driven UI has one hot lookup that a raw join makes
-- expensive at scale. These three materialized views pre-answer them.
--
-- Design rule: none of these bake in CURRENT_DATE. The temporal `validity`
-- column is preserved so read-time can filter for any as-of date without the
-- view going stale purely from the clock advancing. (org_tree_current does
-- filter by CURRENT_DATE; that is technical debt in the existing view, not
-- a pattern to repeat.)
--
-- Refresh after ingest (all support CONCURRENT because each has a unique
-- index):
--   REFRESH MATERIALIZED VIEW CONCURRENTLY person_anchor;
--   REFRESH MATERIALIZED VIEW CONCURRENTLY person_coauthor_edges;
--   REFRESH MATERIALIZED VIEW CONCURRENTLY org_current_roster;
-- ---------------------------------------------------------------------------

-- person_anchor: one row per chart-anchored affiliation. A hop that needs
-- "where does person X hang in the tree as of :d" picks the row where
-- validity @> :d, preferring is_primary then most-recent starts_at.
CREATE MATERIALIZED VIEW person_anchor AS
SELECT
  pa.person_id,
  pa.id             AS affiliation_id,
  pa.title,
  pa.position_rank,
  pa.is_primary,
  pa.validity,
  aoa.organization_id
FROM person_affiliations pa
JOIN affiliation_org_assignments aoa
  ON aoa.affiliation_id = pa.id
WHERE aoa.assignment_type = 'chart_anchor';

CREATE UNIQUE INDEX person_anchor_affiliation ON person_anchor (affiliation_id);
CREATE INDEX person_anchor_person ON person_anchor (person_id);
CREATE INDEX person_anchor_org ON person_anchor (organization_id);
CREATE INDEX person_anchor_validity ON person_anchor USING gist (validity);

-- person_coauthor_edges: canonical undirected pair-weights.
-- Replaces the publication_authors self-join across every paper. One indexed
-- range scan returns top-K coauthors of a person.
CREATE MATERIALIZED VIEW person_coauthor_edges AS
SELECT
  LEAST(a.person_id, b.person_id)    AS person_a,
  GREATEST(a.person_id, b.person_id) AS person_b,
  count(*)                           AS paper_count,
  min(p.publication_year)            AS first_year,
  max(p.publication_year)            AS last_year
FROM publication_authors a
JOIN publication_authors b
  ON b.publication_id = a.publication_id
 AND b.person_id > a.person_id
JOIN publications p ON p.id = a.publication_id
GROUP BY 1, 2;

CREATE UNIQUE INDEX person_coauthor_edges_pair
  ON person_coauthor_edges (person_a, person_b);
-- Two half-indexes so top-K can be served from either side of the canonical
-- pair without an OR condition that neither index alone can satisfy.
CREATE INDEX person_coauthor_edges_a_top
  ON person_coauthor_edges (person_a, paper_count DESC);
CREATE INDEX person_coauthor_edges_b_top
  ON person_coauthor_edges (person_b, paper_count DESC);

-- org_current_roster: chart-anchored people at each org with a stable sort
-- key for keyset pagination. `sort_key` is derived from lastname, firstname
-- so it is deterministic and the same across processes.
CREATE MATERIALIZED VIEW org_current_roster AS
SELECT
  aoa.organization_id,
  pa.person_id,
  pa.id                                                    AS affiliation_id,
  pe.firstname,
  pe.middlename,
  pe.lastname,
  lower(pe.lastname) || E'\t' || lower(pe.firstname)       AS sort_key,
  pa.title,
  pa.position_rank,
  pa.is_primary,
  pa.validity,
  pe.claimed_status
FROM person_affiliations pa
JOIN affiliation_org_assignments aoa ON aoa.affiliation_id = pa.id
JOIN people pe ON pe.id = pa.person_id
WHERE aoa.assignment_type = 'chart_anchor';

-- Uniqueness by (organization_id, affiliation_id) rather than person_id: two
-- concurrent joint appointments would each be their own row and neither
-- should evict the other.
CREATE UNIQUE INDEX org_current_roster_id
  ON org_current_roster (organization_id, affiliation_id);
-- The composite pagination cursor: (org, sort_key, person_id) is the
-- comparison used by  WHERE (sort_key, person_id) > (:cur_sort, :cur_pid).
CREATE INDEX org_current_roster_page
  ON org_current_roster (organization_id, sort_key, person_id);
CREATE INDEX org_current_roster_rank
  ON org_current_roster (organization_id, position_rank)
  WHERE position_rank IS NOT NULL;
CREATE INDEX org_current_roster_person ON org_current_roster (person_id);
CREATE INDEX org_current_roster_validity
  ON org_current_roster USING gist (validity);


-- -----------------------------------------------------------------------------
-- Person 2D projections (scatter canvas)
-- -----------------------------------------------------------------------------
-- `embedding_runs` — one row per full offline projection build. Exactly one
-- row has `is_active = TRUE` at any time (enforced by partial unique index).
--
-- `person_projections_2d` — the 2D coordinates for a given run. The raw high-
-- dim vector is intentionally NOT stored here; adding pgvector later is a
-- schema-additive change.

CREATE TABLE embedding_runs (
  id           BIGSERIAL PRIMARY KEY,
  kind         TEXT      NOT NULL,
  algorithm    TEXT      NOT NULL,
  raw_dim      INTEGER   NOT NULL,
  point_count  INTEGER   NOT NULL DEFAULT 0,
  is_active    BOOLEAN   NOT NULL DEFAULT FALSE,
  notes        TEXT,
  created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT embedding_runs_dim_positive CHECK (raw_dim > 0)
);

CREATE UNIQUE INDEX embedding_runs_one_active
  ON embedding_runs (is_active) WHERE is_active;

CREATE TABLE person_projections_2d (
  run_id     BIGINT NOT NULL,
  person_id  BIGINT NOT NULL,
  x          DOUBLE PRECISION NOT NULL,
  y          DOUBLE PRECISION NOT NULL,
  PRIMARY KEY (run_id, person_id)
);

CREATE INDEX idx_person_projections_2d_person
  ON person_projections_2d (person_id);
