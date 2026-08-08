-- Seed data mirroring src/data/relationalData.ts and src/data/normalizedOrgData.ts.
--
-- schema.sql rebuilds every object it owns, so no TRUNCATE is needed here.

\i schema.sql

-- ---------------------------------------------------------------------------
-- Organizations
--
-- Academic units, the demo company, and a funder all live in one table.
-- ---------------------------------------------------------------------------

INSERT INTO organizations (id, name, short_name, country, kind, description) VALUES
  ('inst-uoft',            'University of Toronto',                 NULL,       'CA', 'university', NULL),
  ('inst-mit',             'Massachusetts Institute of Technology', 'MIT',      'US', 'university', NULL),
  ('dept-mit-eecs',        'MIT Electrical Engineering and Computer Science', 'MIT EECS', 'US', 'department', NULL),
  ('lab-mit-hartwell',     'Hartwell Theory Group',                 NULL,       'US', 'lab',        NULL),
  ('inst-stanford',        'Stanford University',                   NULL,       'US', 'university', NULL),
  ('inst-ucla',            'University of California, Los Angeles', 'UCLA',     'US', 'university', NULL),
  ('inst-berkeley',        'University of California, Berkeley',    NULL,       'US', 'university', NULL),
  ('dept-berkeley-mcb',    'UC Berkeley Molecular and Cell Biology', 'MCB',     'US', 'department', NULL),
  ('lab-berkeley-doudna',  'Doudna Lab',                            NULL,       'US', 'lab',        NULL),
  ('inst-tsinghua',        'Tsinghua University',                   NULL,       'CN', 'university', NULL),
  ('inst-peking',          'Peking University',                     NULL,       'CN', 'university', NULL),
  ('inst-uchicago',        'University of Chicago',                 NULL,       'US', 'university', NULL),
  ('inst-eth',             'ETH Zurich',                            NULL,       'CH', 'university', NULL),
  ('company-editas-demo',  'Editas Demo Labs',                      NULL,       'US', 'company',    'Fictional biotech for prototype.'),
  ('org-nsf',              'National Science Foundation',           'NSF',      'US', 'funder',     NULL),
  ('org-rsas',             'Royal Swedish Academy of Sciences',     NULL,       'SE', 'nonprofit',  NULL);

-- Hierarchy as temporal edges. Open-ended ranges mean "still true today".
INSERT INTO org_relationships (id, child_org_id, parent_org_id, relationship_type, starts_at, verification_status) VALUES
  ('orgrel-mit-eecs',        'dept-mit-eecs',       'inst-mit',          'primary', NULL, 'verified'),
  ('orgrel-mit-hartwell',    'lab-mit-hartwell',    'dept-mit-eecs',     'primary', NULL, 'verified'),
  ('orgrel-berkeley-mcb',    'dept-berkeley-mcb',   'inst-berkeley',     'primary', NULL, 'verified'),
  ('orgrel-berkeley-doudna', 'lab-berkeley-doudna', 'dept-berkeley-mcb', 'primary', NULL, 'verified');

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
  ('person-alice',           'Alice',      NULL, 'Chen',         NULL, 'unclaimed'),
  ('person-wei-tsinghua',    'Wei',        NULL, 'Zhang',        NULL, 'unclaimed'),
  ('person-wei-chicago',     'Wei',        NULL, 'Zhang',        NULL, 'unclaimed'),
  ('person-emmanuel',        'Emmanuelle', NULL, 'Charpentier',  NULL, 'unclaimed'),
  ('person-jennifer',        'Jennifer',   NULL, 'Doudna',
     'Demo profile: awards are conferrals in person_awards, not graph nodes.', 'verified'),
  ('person-advisor-mit',     'Robert',     NULL, 'Hartwell',     NULL, 'unclaimed'),
  ('person-senior-advisor',  'Patricia',   NULL, 'Lang',         NULL, 'unclaimed'),
  ('person-bob-student',     'Bob',        NULL, 'Okonkwo',      NULL, 'unclaimed'),
  ('person-carol-collab',    'Carol',      NULL, 'Mendez',       NULL, 'unclaimed'),
  ('person-david-collab',    'David',      NULL, 'Kim',          NULL, 'unclaimed'),
  ('person-maria-postdoc',   'Maria',      NULL, 'Santos',       NULL, 'unclaimed'),
  ('person-james-ap',        'James',      NULL, 'Wu',           NULL, 'unclaimed');

INSERT INTO person_aliases (person_id, alias) VALUES
  ('person-alice', 'A. Chen');

INSERT INTO person_concepts (person_id, concept_id, rank) VALUES
  ('person-alice',          'computational-biology', 1),
  ('person-alice',          'machine-learning',      2),
  ('person-wei-tsinghua',   'chemistry',             1),
  ('person-wei-tsinghua',   'materials-science',     2),
  ('person-wei-chicago',    'computational-biology', 1),
  ('person-emmanuel',       'microbiology',          1),
  ('person-emmanuel',       'crispr',                2),
  ('person-jennifer',       'biochemistry',          1),
  ('person-jennifer',       'crispr',                2),
  ('person-advisor-mit',    'computer-science',      1),
  ('person-advisor-mit',    'algorithms',            2),
  ('person-senior-advisor', 'theoretical-cs',        1),
  ('person-bob-student',    'machine-learning',      1),
  ('person-carol-collab',   'computational-biology', 1),
  ('person-carol-collab',   'genomics',              2),
  ('person-david-collab',   'statistics',            1),
  ('person-maria-postdoc',  'crispr',                1),
  ('person-maria-postdoc',  'biochemistry',          2),
  ('person-james-ap',       'molecular-biology',     1);

-- ---------------------------------------------------------------------------
-- Awards
--
-- The 2020 chemistry prize was shared. Recording it as two conferrals of one
-- award makes the co-recipient link queryable, which a per-person blob could
-- not express.
-- ---------------------------------------------------------------------------

INSERT INTO awards (id, name, awarding_org_id) VALUES
  ('award-nobel-chemistry', 'Nobel Prize in Chemistry', 'org-rsas');

INSERT INTO person_awards (id, person_id, award_id, awarded_at, verification_status) VALUES
  ('personaward-jennifer-nobel', 'person-jennifer', 'award-nobel-chemistry', '2020-01-01T00:00:00Z', 'verified'),
  ('personaward-emmanuel-nobel', 'person-emmanuel', 'award-nobel-chemistry', '2020-01-01T00:00:00Z', 'verified');

-- ---------------------------------------------------------------------------
-- Affiliations
--
-- `founder` is an affiliation_kind, so Jennifer's company founding is a row
-- here rather than in a separate founded_relationships table.
-- ---------------------------------------------------------------------------

-- `title` is the string as the source printed it; `position_rank` is its
-- normalized form. Jennifer's founding is a concurrent non-primary tie, which
-- is why precedence is its own column rather than a kind.
INSERT INTO person_affiliations
  (id, person_id, title, affiliation_kind, position_rank, is_primary, starts_at, verification_status)
VALUES
  ('position-alice-berkeley',  'person-alice',          'Associate Professor',     'employment', 'associate_professor',    TRUE,  '2024-07-01T00:00:00Z', 'verified'),
  ('position-bob-mit',         'person-bob-student',    'Ph.D. student',           'education',  'phd_student',            TRUE,  '2020-09-01T00:00:00Z', 'verified'),
  ('position-carol-berkeley',  'person-carol-collab',   'Research Scientist',      'employment', 'research_scientist',     TRUE,  '2018-01-01T00:00:00Z', 'verified'),
  ('position-david-uchicago',  'person-david-collab',   'Assistant Professor',     'employment', 'assistant_professor',    TRUE,  '2019-01-01T00:00:00Z', 'verified'),
  ('position-maria-doudna',    'person-maria-postdoc',  'Postdoctoral Fellow',     'employment', 'postdoc',                TRUE,  '2022-01-01T00:00:00Z', 'verified'),
  ('position-james-mcb',       'person-james-ap',       'Adjunct Professor',       'employment', 'adjunct_professor',      FALSE, '2016-01-01T00:00:00Z', 'verified'),
  ('position-wei-tsinghua',    'person-wei-tsinghua',   'Professor of Chemistry',  'employment', 'full_professor',         TRUE,  '2015-01-01T00:00:00Z', 'verified'),
  ('position-wei-chicago',     'person-wei-chicago',    'Postdoctoral Researcher', 'employment', 'postdoc',                TRUE,  '2020-01-01T00:00:00Z', 'verified'),
  ('position-jennifer-doudna', 'person-jennifer',       'Principal Investigator',  'employment', 'principal_investigator', TRUE,  '2002-01-01T00:00:00Z', 'verified'),
  ('position-robert-hartwell', 'person-advisor-mit',    'Professor',               'employment', 'full_professor',         TRUE,  '2005-01-01T00:00:00Z', 'verified'),
  ('position-patricia-lang',   'person-senior-advisor', 'Professor Emerita',       'employment', 'emeritus_professor',     TRUE,  '1990-01-01T00:00:00Z', 'verified'),
  ('affil-jennifer-editas',    'person-jennifer',       'Founder',                 'founding',   NULL,                     FALSE, '2013-01-01T00:00:00Z', 'verified');

INSERT INTO affiliation_org_assignments (affiliation_id, organization_id, assignment_type) VALUES
  ('position-alice-berkeley',  'dept-berkeley-mcb',   'chart_anchor'),
  ('position-bob-mit',         'lab-mit-hartwell',    'chart_anchor'),
  ('position-carol-berkeley',  'inst-berkeley',       'chart_anchor'),
  ('position-david-uchicago',  'inst-uchicago',       'chart_anchor'),
  ('position-maria-doudna',    'lab-berkeley-doudna', 'chart_anchor'),
  ('position-james-mcb',       'dept-berkeley-mcb',   'chart_anchor'),
  ('position-wei-tsinghua',    'inst-tsinghua',       'chart_anchor'),
  ('position-wei-chicago',     'inst-uchicago',       'chart_anchor'),
  ('position-jennifer-doudna', 'lab-berkeley-doudna', 'chart_anchor'),
  ('position-robert-hartwell', 'dept-mit-eecs',       'chart_anchor'),
  ('position-patricia-lang',   'dept-mit-eecs',       'chart_anchor'),
  ('affil-jennifer-editas',    'company-editas-demo', 'chart_anchor');

-- ---------------------------------------------------------------------------
-- Person relationships
--
-- `collaborated_with` is symmetric, so rel-jennifer-emmanuelle is stored with
-- the endpoints in canonical id order rather than as authored.
-- ---------------------------------------------------------------------------

INSERT INTO person_relationships (id, type, from_person_id, to_person_id, verification_status) VALUES
  ('rel-alice-advisor',        'advised_by',        'person-alice',         'person-advisor-mit',    'verified'),
  ('rel-bob-advisor',          'advised_by',        'person-bob-student',   'person-advisor-mit',    'verified'),
  ('rel-advisor-senior',       'advised_by',        'person-advisor-mit',   'person-senior-advisor', 'verified'),
  ('rel-maria-jennifer',       'advised_by',        'person-maria-postdoc', 'person-jennifer',       'verified'),
  ('rel-jennifer-emmanuelle',  'collaborated_with', 'person-emmanuel',      'person-jennifer',       'verified');

-- ---------------------------------------------------------------------------
-- Publications
-- ---------------------------------------------------------------------------

INSERT INTO publications (id, title, publication_year) VALUES
  ('pub-alice-carol-1',        'Graph regularization for single-cell trajectory inference', 2021),
  ('pub-alice-carol-2',        'Latent embeddings for multi-omics integration',             2023),
  ('pub-alice-carol-3',        'Robust batch correction in spatial transcriptomics',        2024),
  ('pub-alice-david-1',        'Bayesian priors for sparse regulatory networks',            2022),
  ('pub-jennifer-emmanuel-1',  'CRISPR-Cas9 genome engineering (demo)',                     2012),
  ('pub-maria-jennifer-1',     'Guide RNA design benchmarks',                               2023);

INSERT INTO publication_concepts (publication_id, concept_id) VALUES
  ('pub-alice-carol-1',       'computational-biology'),
  ('pub-alice-carol-2',       'machine-learning'),
  ('pub-alice-carol-3',       'genomics'),
  ('pub-alice-david-1',       'statistics'),
  ('pub-jennifer-emmanuel-1', 'crispr'),
  ('pub-maria-jennifer-1',    'crispr');

INSERT INTO publication_authors (publication_id, person_id, author_position) VALUES
  ('pub-alice-carol-1',       'person-alice',         1),
  ('pub-alice-carol-1',       'person-carol-collab',  2),
  ('pub-alice-carol-2',       'person-alice',         1),
  ('pub-alice-carol-2',       'person-carol-collab',  2),
  ('pub-alice-carol-3',       'person-carol-collab',  1),
  ('pub-alice-carol-3',       'person-alice',         2),
  ('pub-alice-david-1',       'person-alice',         1),
  ('pub-alice-david-1',       'person-david-collab',  2),
  ('pub-jennifer-emmanuel-1', 'person-jennifer',      1),
  ('pub-jennifer-emmanuel-1', 'person-emmanuel',      2),
  ('pub-maria-jennifer-1',    'person-maria-postdoc', 1),
  ('pub-maria-jennifer-1',    'person-jennifer',      2);

-- Affiliation as printed on the paper.
INSERT INTO publication_author_affiliations
  (id, publication_id, person_id, organization_id, verification_status)
VALUES
  ('pubaff-alice-1',    'pub-alice-carol-1',       'person-alice',    'dept-berkeley-mcb',   'verified'),
  ('pubaff-jennifer-1', 'pub-jennifer-emmanuel-1', 'person-jennifer', 'lab-berkeley-doudna', 'verified');

INSERT INTO publication_citations (citing_publication_id, cited_publication_id) VALUES
  ('pub-maria-jennifer-1', 'pub-jennifer-emmanuel-1'),
  ('pub-alice-carol-3',    'pub-alice-carol-1');

-- ---------------------------------------------------------------------------
-- Funding
-- ---------------------------------------------------------------------------

INSERT INTO grants (id, title, funder_org_id, award_number, amount, currency, starts_at, ends_at, verification_status) VALUES
  ('grant-nsf-crispr', 'Programmable genome editing at scale (demo)', 'org-nsf', 'NSF-1234567', 750000.00, 'USD', '2022-09-01T00:00:00Z', '2026-08-31T00:00:00Z', 'verified');

INSERT INTO grant_participants (grant_id, person_id, organization_id, role) VALUES
  ('grant-nsf-crispr', 'person-jennifer',      'lab-berkeley-doudna', 'principal_investigator'),
  ('grant-nsf-crispr', 'person-maria-postdoc', 'lab-berkeley-doudna', 'co_investigator');

-- ---------------------------------------------------------------------------
-- Identifiers and evidence
-- ---------------------------------------------------------------------------

INSERT INTO external_identifiers (id, provider, external_id, person_id) VALUES
  ('extid-alice-orcid',        'orcid', '0000-0002-1825-0097', 'person-alice'),
  ('extid-wei-tsinghua-orcid', 'orcid', '0000-0001-2345-6789', 'person-wei-tsinghua'),
  ('extid-wei-chicago-orcid',  'orcid', '0000-0009-8765-4321', 'person-wei-chicago');

INSERT INTO external_identifiers (id, provider, external_id, publication_id) VALUES
  ('extid-pub1-doi', 'doi', '10.1000/demo.001', 'pub-alice-carol-1'),
  ('extid-pub2-doi', 'doi', '10.1000/demo.002', 'pub-alice-carol-2'),
  ('extid-pub3-doi', 'doi', '10.1000/demo.003', 'pub-alice-carol-3'),
  ('extid-pub4-doi', 'doi', '10.1000/demo.004', 'pub-alice-david-1'),
  ('extid-pub5-doi', 'doi', '10.1000/demo.005', 'pub-jennifer-emmanuel-1'),
  ('extid-pub6-doi', 'doi', '10.1000/demo.006', 'pub-maria-jennifer-1');

-- Every cited URL is fetched once and lives here. Evidence rows below point at
-- these by id rather than repeating the string.
INSERT INTO source_snapshots (id, source_url, source_kind, fetched_at, content_hash, http_status) VALUES
  ('snap-mcb-roster', 'https://example.edu/mcb/people',      'official_roster', '2026-01-15T12:00:00Z', 'sha256:demo-roster',   200),
  ('snap-thesis',     'https://example.edu/thesis',          'manual',           '2026-01-15T12:00:00Z', 'sha256:demo-thesis',   200),
  ('snap-advising',   'https://example.edu/advising',        'official_roster',  '2026-01-15T12:00:00Z', 'sha256:demo-advising', 200),
  ('snap-cv',         'https://example.edu/cv',              'official_profile', '2026-01-15T12:00:00Z', 'sha256:demo-cv',       200),
  ('snap-lab',        'https://example.edu/lab',             'official_roster',  '2026-01-15T12:00:00Z', 'sha256:demo-lab',      200),
  ('snap-crispr',     'https://example.edu/crispr',          'crossref',         '2026-01-15T12:00:00Z', 'sha256:demo-crispr',   200),
  ('snap-nobel',      'https://example.org/nobel/2020',      'news',             '2026-01-15T12:00:00Z', 'sha256:demo-nobel',    200),
  ('snap-nsf-award',  'https://example.gov/awards/1234567',  'manual',           '2026-01-15T12:00:00Z', 'sha256:demo-nsf',      200);

-- Each row cites exactly one fact; the arc column says which.
INSERT INTO evidence (id, label, snapshot_id, affiliation_id) VALUES
  ('ev-alice-position', 'Department roster', 'snap-mcb-roster', 'position-alice-berkeley');

INSERT INTO evidence (id, label, snapshot_id, person_relationship_id) VALUES
  ('ev-alice-advisor',       'Thesis',          'snap-thesis',   'rel-alice-advisor'),
  ('ev-bob-advisor',         'Advising record', 'snap-advising', 'rel-bob-advisor'),
  ('ev-advisor-senior',      'CV',              'snap-cv',       'rel-advisor-senior'),
  ('ev-maria-jennifer',      'Lab mentorship',  'snap-lab',      'rel-maria-jennifer'),
  ('ev-jennifer-emmanuelle', 'Coauthored work', 'snap-crispr',   'rel-jennifer-emmanuelle');

INSERT INTO evidence (id, label, snapshot_id, person_award_id) VALUES
  ('ev-jennifer-nobel', 'Prize announcement', 'snap-nobel', 'personaward-jennifer-nobel'),
  ('ev-emmanuel-nobel', 'Prize announcement', 'snap-nobel', 'personaward-emmanuel-nobel');

INSERT INTO evidence (id, label, snapshot_id, grant_id) VALUES
  ('ev-nsf-grant', 'Award abstract', 'snap-nsf-award', 'grant-nsf-crispr');

-- The chart reads this projection, so refresh it after loading.
REFRESH MATERIALIZED VIEW org_tree_current;
