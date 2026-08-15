-- Legacy seed data mirroring the original prototype relational dataset.
--
-- This file is DATA ONLY and is parsed by scripts/db/restore_legacy_seed.py,
-- which translates the old text-slug ids into the current BIGINT-identity
-- schema.  Do NOT load it directly with psql: the slug ids are not valid
-- BIGINT values.
--
-- Apply db/schema.sql first, then run:
--   python3 -m scripts.db.restore_legacy_seed
-- ---------------------------------------------------------------------------
-- Organizations
--
-- Academic units, the demo company, and a funder all live in one table.
-- ---------------------------------------------------------------------------

INSERT INTO organizations (id, name, short_name, country, kind, description) VALUES
  ('inst-uoft',            'University of Toronto',                 NULL,       'CA', 'university', NULL),
  ('inst-mit',             'Massachusetts Institute of Technology', 'MIT',      'US', 'university', NULL),
  ('dept-mit-eecs',        'MIT Electrical Engineering and Computer Science', 'MIT EECS', 'US', 'department', NULL),
  ('lab-mit-demo',     'Demo Theory Group',                 NULL,       'US', 'lab',        NULL),
  ('inst-stanford',        'Stanford University',                   NULL,       'US', 'university', NULL),
  ('inst-ucla',            'University of California, Los Angeles', 'UCLA',     'US', 'university', NULL),
  ('inst-berkeley',        'University of California, Berkeley',    NULL,       'US', 'university', NULL),
  ('dept-berkeley-mcb',    'UC Berkeley Molecular and Cell Biology', 'MCB',     'US', 'department', NULL),
  ('lab-berkeley-demo',  'Demo Research Lab',                            NULL,       'US', 'lab',        NULL),
  ('inst-tsinghua',        'Tsinghua University',                   NULL,       'CN', 'university', NULL),
  ('inst-peking',          'Peking University',                     NULL,       'CN', 'university', NULL),
  ('inst-uchicago',        'University of Chicago',                 NULL,       'US', 'university', NULL),
  ('inst-eth',             'ETH Zurich',                            NULL,       'CH', 'university', NULL),
  ('company-demo-labs',  'Demo Labs',                      NULL,       'US', 'company',    'Fictional biotech for prototype.'),
  ('org-nsf',              'National Science Foundation',           'NSF',      'US', 'funder',     NULL),
  ('org-demo-society',             'Demo Scientific Society',     NULL,       'SE', 'nonprofit',  NULL);

-- Hierarchy as temporal edges. Open-ended ranges mean "still true today".
INSERT INTO org_relationships (id, child_org_id, parent_org_id, relationship_type, starts_at, verification_status) VALUES
  ('orgrel-mit-eecs',        'dept-mit-eecs',       'inst-mit',          'primary', NULL, 'verified'),
  ('orgrel-mit-demo',    'lab-mit-demo',    'dept-mit-eecs',     'primary', NULL, 'verified'),
  ('orgrel-berkeley-mcb',    'dept-berkeley-mcb',   'inst-berkeley',     'primary', NULL, 'verified'),
  ('orgrel-berkeley-demo', 'lab-berkeley-demo', 'dept-berkeley-mcb', 'primary', NULL, 'verified');

-- ---------------------------------------------------------------------------
-- Concepts
-- ---------------------------------------------------------------------------

INSERT INTO concepts (id, display_name, parent_id, level) VALUES
  ('computer-science',      'Computer Science',      NULL,               0),
  ('biology',               'Biology',               NULL,               0),
  ('chemistry',             'Chemistry',             NULL,               0),
  ('statistics',            'Statistics',            NULL,               0),
  ('machine-learning',      'Machine Learning',      'computer-science', 1),
  ('algorithms',            'Algorithms',            'computer-science', 1),
  ('theoretical-cs',        'Theoretical CS',        'computer-science', 1),
  ('computational-biology', 'Computational Biology', 'biology',          1),
  ('molecular-biology',     'Molecular Biology',     'biology',          1),
  ('microbiology',          'Microbiology',          'biology',          1),
  ('genomics',              'Genomics',              'biology',          1),
  ('biochemistry',          'Biochemistry',          'biology',          1),
  ('materials-science',     'Materials Science',     'chemistry',        1),
  ('crispr',                'CRISPR',                'molecular-biology', 2);

-- ---------------------------------------------------------------------------
-- People
-- ---------------------------------------------------------------------------

INSERT INTO people (id, firstname, middlename, lastname, biography, claimed_status) VALUES
  ('person-a',           'Casey',      NULL, 'Doe',         NULL, 'unclaimed'),
  ('person-b-tsinghua',    'Jamie',        NULL, 'Doe',        NULL, 'unclaimed'),
  ('person-b-chicago',     'Jamie',        NULL, 'Doe',        NULL, 'unclaimed'),
  ('person-c',        'Taylor', NULL, 'Doe',  NULL, 'unclaimed'),
  ('person-d',        'Avery',   NULL, 'Doe',
     'Demo profile: awards are conferrals in person_awards, not graph nodes.', 'verified'),
  ('person-e',     'Robin',     NULL, 'Doe',     NULL, 'unclaimed'),
  ('person-f',  'Pat',   NULL, 'Doe',         NULL, 'unclaimed'),
  ('person-g',     'Bobby',        NULL, 'Demo',      NULL, 'unclaimed'),
  ('person-h',    'Charlie',      NULL, 'Demo',       NULL, 'unclaimed'),
  ('person-i',    'Drew',      NULL, 'Demo',          NULL, 'unclaimed'),
  ('person-j',   'Mika',      NULL, 'Demo',       NULL, 'unclaimed'),
  ('person-k',        'Jordan',      NULL, 'Demo',           NULL, 'unclaimed');

INSERT INTO person_aliases (person_id, alias) VALUES
  ('person-a', 'A. Doe');

INSERT INTO person_concepts (person_id, concept_id, rank) VALUES
  ('person-a',          'computational-biology', 1),
  ('person-a',          'machine-learning',      2),
  ('person-b-tsinghua',   'chemistry',             1),
  ('person-b-tsinghua',   'materials-science',     2),
  ('person-b-chicago',    'computational-biology', 1),
  ('person-c',       'microbiology',          1),
  ('person-c',       'crispr',                2),
  ('person-d',       'biochemistry',          1),
  ('person-d',       'crispr',                2),
  ('person-e',    'computer-science',      1),
  ('person-e',    'algorithms',            2),
  ('person-f', 'theoretical-cs',        1),
  ('person-g',    'machine-learning',      1),
  ('person-h',   'computational-biology', 1),
  ('person-h',   'genomics',              2),
  ('person-i',   'statistics',            1),
  ('person-j',  'crispr',                1),
  ('person-j',  'biochemistry',          2),
  ('person-k',       'molecular-biology',     1);

-- ---------------------------------------------------------------------------
-- Awards
--
-- The demo prize was shared. Recording it as two conferrals of one
-- award makes the co-recipient link queryable, which a per-person blob could
-- not express.
-- ---------------------------------------------------------------------------

INSERT INTO awards (id, name, awarding_org_id) VALUES
  ('award-demo-prize', 'Demo Prize in Science', 'org-demo-society');

INSERT INTO person_awards (id, person_id, award_id, awarded_at, verification_status) VALUES
  ('personaward-d-demo', 'person-d', 'award-demo-prize', '2021-01-01T00:00:00Z', 'verified'),
  ('personaward-c-demo', 'person-c', 'award-demo-prize', '2021-01-01T00:00:00Z', 'verified');

-- ---------------------------------------------------------------------------
-- Affiliations
--
-- `founder` is an affiliation_kind, so Avery's company founding is a row
-- here rather than in a separate founded_relationships table.
-- ---------------------------------------------------------------------------

-- `title` is the string as the source printed it; `position_rank` is its
-- normalized form. Avery's founding is a concurrent non-primary tie, which
-- is why precedence is its own column rather than a kind.
INSERT INTO person_affiliations
  (id, person_id, title, affiliation_kind, position_rank, is_primary, starts_at, verification_status)
VALUES
  ('position-a-berkeley',  'person-a',          'Associate Professor',     'employment', 'associate_professor',    TRUE,  '2024-07-01T00:00:00Z', 'verified'),
  ('position-g-mit',         'person-g',    'Ph.D. student',           'education',  'phd_student',            TRUE,  '2020-09-01T00:00:00Z', 'verified'),
  ('position-h-berkeley',  'person-h',   'Research Scientist',      'employment', 'research_scientist',     TRUE,  '2018-01-01T00:00:00Z', 'verified'),
  ('position-i-uchicago',  'person-i',   'Assistant Professor',     'employment', 'assistant_professor',    TRUE,  '2019-01-01T00:00:00Z', 'verified'),
  ('position-j-demo-lab',    'person-j',  'Postdoctoral Fellow',     'employment', 'postdoc',                TRUE,  '2022-01-01T00:00:00Z', 'verified'),
  ('position-k-mcb',       'person-k',       'Adjunct Professor',       'employment', 'adjunct_professor',      FALSE, '2016-01-01T00:00:00Z', 'verified'),
  ('position-b-tsinghua',    'person-b-tsinghua',   'Professor of Chemistry',  'employment', 'full_professor',         TRUE,  '2015-01-01T00:00:00Z', 'verified'),
  ('position-b-chicago',     'person-b-chicago',    'Postdoctoral Researcher', 'employment', 'postdoc',                TRUE,  '2021-01-01T00:00:00Z', 'verified'),
  ('position-d-demo-lab', 'person-d',       'Principal Investigator',  'employment', 'principal_investigator', TRUE,  '2002-01-01T00:00:00Z', 'verified'),
  ('position-e-mit', 'person-e',    'Professor',               'employment', 'full_professor',         TRUE,  '2005-01-01T00:00:00Z', 'verified'),
  ('position-f-mit',   'person-f', 'Professor Emerita',       'employment', 'emeritus_professor',     TRUE,  '1990-01-01T00:00:00Z', 'verified'),
  ('affil-d-demo-company',    'person-d',       'Founder',                 'founding',   NULL,                     FALSE, '2013-01-01T00:00:00Z', 'verified');

INSERT INTO affiliation_org_assignments (affiliation_id, organization_id, assignment_type) VALUES
  ('position-a-berkeley',  'dept-berkeley-mcb',   'chart_anchor'),
  ('position-g-mit',         'lab-mit-demo',    'chart_anchor'),
  ('position-h-berkeley',  'inst-berkeley',       'chart_anchor'),
  ('position-i-uchicago',  'inst-uchicago',       'chart_anchor'),
  ('position-j-demo-lab',    'lab-berkeley-demo', 'chart_anchor'),
  ('position-k-mcb',       'dept-berkeley-mcb',   'chart_anchor'),
  ('position-b-tsinghua',    'inst-tsinghua',       'chart_anchor'),
  ('position-b-chicago',     'inst-uchicago',       'chart_anchor'),
  ('position-d-demo-lab', 'lab-berkeley-demo', 'chart_anchor'),
  ('position-e-mit', 'dept-mit-eecs',       'chart_anchor'),
  ('position-f-mit',   'dept-mit-eecs',       'chart_anchor'),
  ('affil-d-demo-company',    'company-demo-labs', 'chart_anchor');

-- ---------------------------------------------------------------------------
-- Person relationships
--
-- `collaborated_with` is symmetric, so rel-d-c is stored with
-- the endpoints in canonical id order rather than as authored.
-- ---------------------------------------------------------------------------

INSERT INTO person_relationships (id, type, from_person_id, to_person_id, verification_status) VALUES
  ('rel-a-e',        'advised_by',        'person-a',         'person-e',    'verified'),
  ('rel-g-e',          'advised_by',        'person-g',   'person-e',    'verified'),
  ('rel-e-f',       'advised_by',        'person-e',   'person-f', 'verified'),
  ('rel-j-d',       'advised_by',        'person-j', 'person-d',       'verified'),
  ('rel-d-c',  'collaborated_with', 'person-c',      'person-d',       'verified');

-- ---------------------------------------------------------------------------
-- Publications
-- ---------------------------------------------------------------------------

INSERT INTO publications (id, title, publication_year) VALUES
  ('pub-a-h-1',        'Graph regularization for single-cell trajectory inference', 2021),
  ('pub-a-h-2',        'Latent embeddings for multi-omics integration',             2023),
  ('pub-a-h-3',        'Robust batch correction in spatial transcriptomics',        2024),
  ('pub-a-i-1',        'Bayesian priors for sparse regulatory networks',            2022),
  ('pub-d-c-1',  'CRISPR-Cas9 genome engineering (demo)',                     2012),
  ('pub-j-d-1',     'Guide RNA design benchmarks',                               2023);

INSERT INTO publication_concepts (publication_id, concept_id) VALUES
  ('pub-a-h-1',       'computational-biology'),
  ('pub-a-h-2',       'machine-learning'),
  ('pub-a-h-3',       'genomics'),
  ('pub-a-i-1',       'statistics'),
  ('pub-d-c-1', 'crispr'),
  ('pub-j-d-1',    'crispr');

INSERT INTO publication_authors (publication_id, person_id, author_position) VALUES
  ('pub-a-h-1',       'person-a',         1),
  ('pub-a-h-1',       'person-h',  2),
  ('pub-a-h-2',       'person-a',         1),
  ('pub-a-h-2',       'person-h',  2),
  ('pub-a-h-3',       'person-h',  1),
  ('pub-a-h-3',       'person-a',         2),
  ('pub-a-i-1',       'person-a',         1),
  ('pub-a-i-1',       'person-i',  2),
  ('pub-d-c-1', 'person-d',      1),
  ('pub-d-c-1', 'person-c',      2),
  ('pub-j-d-1',    'person-j', 1),
  ('pub-j-d-1',    'person-d',      2);

-- Affiliation as printed on the paper.
INSERT INTO publication_author_affiliations
  (id, publication_id, person_id, organization_id, verification_status)
VALUES
  ('pubaff-a-1',    'pub-a-h-1',       'person-a',    'dept-berkeley-mcb',   'verified'),
  ('pubaff-d-1', 'pub-d-c-1', 'person-d', 'lab-berkeley-demo', 'verified');

INSERT INTO publication_citations (citing_publication_id, cited_publication_id) VALUES
  ('pub-j-d-1', 'pub-d-c-1'),
  ('pub-a-h-3',    'pub-a-h-1');

-- ---------------------------------------------------------------------------
-- Funding
-- ---------------------------------------------------------------------------

INSERT INTO grants (id, title, funder_org_id, award_number, amount, currency, starts_at, ends_at, verification_status) VALUES
  ('grant-nsf-demo', 'Programmable genome editing at scale (demo)', 'org-nsf', 'NSF-1234567', 750000.00, 'USD', '2022-09-01T00:00:00Z', '2026-08-31T00:00:00Z', 'verified');

INSERT INTO grant_participants (grant_id, person_id, organization_id, role) VALUES
  ('grant-nsf-demo', 'person-d',      'lab-berkeley-demo', 'principal_investigator'),
  ('grant-nsf-demo', 'person-j', 'lab-berkeley-demo', 'co_investigator');

-- ---------------------------------------------------------------------------
-- Identifiers and evidence
-- ---------------------------------------------------------------------------

INSERT INTO external_identifiers (id, provider, external_id, person_id) VALUES
  ('extid-a-orcid',        'orcid', '0000-0002-1825-0097', 'person-a'),
  ('extid-b-tsinghua-orcid', 'orcid', '0000-0001-2345-6789', 'person-b-tsinghua'),
  ('extid-b-chicago-orcid',  'orcid', '0000-0009-8765-4321', 'person-b-chicago');

INSERT INTO external_identifiers (id, provider, external_id, publication_id) VALUES
  ('extid-pub1-doi', 'doi', '10.1000/demo.001', 'pub-a-h-1'),
  ('extid-pub2-doi', 'doi', '10.1000/demo.002', 'pub-a-h-2'),
  ('extid-pub3-doi', 'doi', '10.1000/demo.003', 'pub-a-h-3'),
  ('extid-pub4-doi', 'doi', '10.1000/demo.004', 'pub-a-i-1'),
  ('extid-pub5-doi', 'doi', '10.1000/demo.005', 'pub-d-c-1'),
  ('extid-pub6-doi', 'doi', '10.1000/demo.006', 'pub-j-d-1');

-- Every cited URL is fetched once and lives here. Evidence rows below point at
-- these by id rather than repeating the string.
INSERT INTO source_snapshots (id, source_url, source_kind, fetched_at, content_hash, http_status) VALUES
  ('snap-mcb-roster', 'https://example.edu/mcb/people',      'official_roster', '2026-01-15T12:00:00Z', 'sha256:demo-roster',   200),
  ('snap-thesis',     'https://example.edu/thesis',          'manual',           '2026-01-15T12:00:00Z', 'sha256:demo-thesis',   200),
  ('snap-advising',   'https://example.edu/advising',        'official_roster',  '2026-01-15T12:00:00Z', 'sha256:demo-advising', 200),
  ('snap-cv',         'https://example.edu/cv',              'official_profile', '2026-01-15T12:00:00Z', 'sha256:demo-cv',       200),
  ('snap-lab',        'https://example.edu/lab',             'official_roster',  '2026-01-15T12:00:00Z', 'sha256:demo-lab',      200),
  ('snap-crispr',     'https://example.edu/crispr',          'crossref',         '2026-01-15T12:00:00Z', 'sha256:demo-crispr',   200),
  ('snap-demo-prize',      'https://example.org/demo-prize',      'news',             '2026-01-15T12:00:00Z', 'sha256:demo-prize',    200),
  ('snap-nsf-award',  'https://example.gov/awards/1234567',  'manual',           '2026-01-15T12:00:00Z', 'sha256:demo-nsf',      200);

-- Each row cites exactly one fact; the arc column says which.
INSERT INTO evidence (id, label, snapshot_id, affiliation_id) VALUES
  ('ev-a-position', 'Department roster', 'snap-mcb-roster', 'position-a-berkeley');

INSERT INTO evidence (id, label, snapshot_id, person_relationship_id) VALUES
  ('ev-a-e',       'Thesis',          'snap-thesis',   'rel-a-e'),
  ('ev-g-e',         'Advising record', 'snap-advising', 'rel-g-e'),
  ('ev-e-f',      'CV',              'snap-cv',       'rel-e-f'),
  ('ev-j-d',      'Lab mentorship',  'snap-lab',      'rel-j-d'),
  ('ev-d-c', 'Coauthored work', 'snap-crispr',   'rel-d-c');

INSERT INTO evidence (id, label, snapshot_id, person_award_id) VALUES
  ('ev-d-demo-prize', 'Prize announcement', 'snap-demo-prize', 'personaward-d-demo'),
  ('ev-c-demo-prize', 'Prize announcement', 'snap-demo-prize', 'personaward-c-demo');

INSERT INTO evidence (id, label, snapshot_id, grant_id) VALUES
  ('ev-nsf-grant', 'Award abstract', 'snap-nsf-award', 'grant-nsf-demo');

-- The chart reads this projection, so refresh it after loading.
REFRESH MATERIALIZED VIEW org_tree_current;
