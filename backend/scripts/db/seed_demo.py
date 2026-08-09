#!/usr/bin/env python3
"""Deterministic demo dataset generator for local pipeline verification.

Usage::

    python3 -m scripts.db.seed_demo

Two runs must produce identical row counts (idempotent, no RNG).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any

from sqlalchemy import text

from api.deps import _SessionLocal

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

CURRENT_DATE = date.today()
UTC = timezone.utc

# ---------------------------------------------------------------------------
# Name lists (60 each, indexed by person_id - 1)
# ---------------------------------------------------------------------------

FIRST_NAMES = [
    "James", "Mary", "Robert", "Patricia", "John", "Jennifer",
    "Michael", "Linda", "David", "Elizabeth", "William", "Barbara",
    "Richard", "Susan", "Joseph", "Jessica", "Thomas", "Sarah",
    "Christopher", "Karen", "Daniel", "Lisa", "Matthew", "Nancy",
    "Anthony", "Betty", "Mark", "Margaret", "Donald", "Sandra",
    "Steven", "Ashley", "Andrew", "Kimberly", "Paul", "Emily",
    "Joshua", "Donna", "Kenneth", "Michelle", "Kevin", "Carol",
    "Brian", "Amanda", "George", "Melissa", "Timothy", "Deborah",
    "Ronald", "Stephanie", "Jason", "Rebecca", "Edward", "Laura",
    "Jeffrey", "Sharon", "Ryan", "Cynthia", "Jacob", "Kathleen",
]

LAST_NAMES = [
    "Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia",
    "Miller", "Davis", "Rodriguez", "Martinez", "Hernandez", "Lopez",
    "Gonzalez", "Wilson", "Anderson", "Thomas", "Taylor", "Moore",
    "Jackson", "Martin", "Lee", "Perez", "Thompson", "White",
    "Harris", "Sanchez", "Clark", "Ramirez", "Lewis", "Robinson",
    "Walker", "Young", "Allen", "King", "Wright", "Scott",
    "Torres", "Nguyen", "Hill", "Flores", "Green", "Adams",
    "Nelson", "Baker", "Hall", "Rivera", "Campbell", "Mitchell",
    "Carter", "Roberts", "Gomez", "Phillips", "Evans", "Turner",
    "Diaz", "Parker", "Cruz", "Edwards", "Collins", "Reyes",
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dt(year: int, month: int = 1, day: int = 1) -> datetime:
    return datetime(year, month, day, tzinfo=UTC)


def _rank_for(pid: int) -> str:
    """Deterministic position_rank per person id."""
    ranks = (
        "full_professor", "associate_professor", "assistant_professor",
        "postdoc", "phd_student", "research_fellow", "lecturer",
        "full_professor", "associate_professor", "assistant_professor",
        "postdoc", "phd_student", "research_fellow", "lecturer",
        "phd_student",
    )
    return ranks[(pid - 1) % len(ranks)]


def _affiliation_kind_for(pid: int) -> str:
    kinds = ("employment", "employment", "employment", "education", "education")
    return kinds[(pid - 1) % len(kinds)]


# ---------------------------------------------------------------------------
# Delete helpers
# ---------------------------------------------------------------------------

DELETE_ORDER = [
    "person_topics",
    "publication_topics",
    "topics",
    "person_concepts",
    "publication_concepts",
    "concepts",
    "publication_authors",
    "publications",
    "affiliation_org_assignments",
    "person_affiliations",
    "org_relationships",
    "organizations",
    "person_aliases",
    "people",
]


def _delete_all(session: Any) -> None:
    for table in DELETE_ORDER:
        session.execute(text(f"DELETE FROM {table}"))


SEQUENCES = [
    "people_id_seq",
    "publications_id_seq",
    "organizations_id_seq",
    "org_relationships_id_seq",
    "person_affiliations_id_seq",
    "concepts_id_seq",
]


def _reset_sequences(session: Any) -> None:
    for seq in SEQUENCES:
        session.execute(text(f"ALTER SEQUENCE {seq} RESTART WITH 1"))


# ---------------------------------------------------------------------------
# Insert functions
# ---------------------------------------------------------------------------


def _insert_organizations(session: Any) -> None:
    rows: list[dict[str, Any]] = [
        {
            "id": 1,
            "name": "University of California, Berkeley",
            "short_name": "UC Berkeley",
            "kind": "university",
            "country": "US",
            "homepage_url": "https://www.berkeley.edu",
        },
        {
            "id": 2,
            "name": "Stanford University",
            "short_name": "Stanford",
            "kind": "university",
            "country": "US",
            "homepage_url": "https://www.stanford.edu",
        },
        {
            "id": 3,
            "name": "Haas School of Business",
            "short_name": "Haas",
            "kind": "school",
            "country": "US",
            "homepage_url": "https://haas.berkeley.edu",
        },
        {
            "id": 4,
            "name": "Department of Economics",
            "short_name": "Berkeley Economics",
            "kind": "department",
            "country": "US",
            "homepage_url": "https://www.econ.berkeley.edu",
        },
        {
            "id": 5,
            "name": "Graduate School of Business",
            "short_name": "Stanford GSB",
            "kind": "school",
            "country": "US",
            "homepage_url": "https://www.gsb.stanford.edu",
        },
        {
            "id": 6,
            "name": "Department of Economics",
            "short_name": "Stanford Economics",
            "kind": "department",
            "country": "US",
            "homepage_url": "https://economics.stanford.edu",
        },
        {
            "id": 7,
            "name": "Haas Behavioral Research Lab",
            "short_name": "Haas Behavioral Lab",
            "kind": "lab",
            "country": "US",
            "homepage_url": None,
        },
        {
            "id": 8,
            "name": "Haas Economic Policy Lab",
            "short_name": "Haas Policy Lab",
            "kind": "lab",
            "country": "US",
            "homepage_url": None,
        },
        {
            "id": 9,
            "name": "Stanford GSB Corporate Governance Lab",
            "short_name": "Stanford Gov Lab",
            "kind": "lab",
            "country": "US",
            "homepage_url": None,
        },
        {
            "id": 10,
            "name": "Stanford Applied Economics Lab",
            "short_name": "Stanford AE Lab",
            "kind": "lab",
            "country": "US",
            "homepage_url": None,
        },
    ]
    for r in rows:
        session.execute(
            text(
                """
                INSERT INTO organizations
                  (id, name, short_name, kind, country, homepage_url)
                VALUES (:id, :name, :short_name, :kind, :country, :homepage_url)
                """
            ),
            r,
        )


def _insert_org_relationships(session: Any) -> None:
    """Wire org tree with primary relationships and temporal validity.

    Tree shape:
        UC Berkeley (1)
          +-- Haas School of Business (3)
          |     +-- Haas Behavioral Research Lab (7)
          |     +-- Haas Economic Policy Lab (8)
          +-- Department of Economics (4)
        Stanford University (2)
          +-- Graduate School of Business (5)
          |     +-- Stanford GSB Corporate Governance Lab (9)
          |     +-- Stanford Applied Economics Lab (10)
          +-- Department of Economics (6)
    """
    base = _dt(2000, 1, 1)
    rows = [
        (1, 3, 1, base),   # Haas -> UC Berkeley
        (2, 4, 1, base),   # Berkeley Econ Dept -> UC Berkeley
        (3, 5, 2, base),   # GSB -> Stanford
        (4, 6, 2, base),   # Stanford Econ Dept -> Stanford
        (5, 7, 3, base),   # Behavioral Lab -> Haas
        (6, 8, 3, base),   # Policy Lab -> Haas
        (7, 9, 5, base),   # Gov Lab -> Stanford GSB
        (8, 10, 5, base),  # AE Lab -> Stanford GSB
    ]
    for rel_id, child_id, parent_id, starts in rows:
        session.execute(
            text(
                """
                INSERT INTO org_relationships
                  (id, child_org_id, parent_org_id, relationship_type,
                   starts_at, ends_at, verification_status)
                VALUES (:id, :child, :parent, 'primary', :starts, NULL, 'verified')
                """
            ),
            {"id": rel_id, "child": child_id, "parent": parent_id, "starts": starts},
        )


def _insert_people(session: Any) -> None:
    """60 people, 15 per lab.

    - people 1-15  -> org 7  (Haas Behavioral Lab)
    - people 16-30 -> org 8  (Haas Policy Lab)
    - people 31-45 -> org 9  (Stanford Gov Lab)
    - people 46-60 -> org 10 (Stanford AE Lab)

    50 active (validity contains CURRENT_DATE), 10 retired (isolates).
    """
    for pid in range(1, 61):
        session.execute(
            text(
                """
                INSERT INTO people (id, firstname, lastname, claimed_status)
                VALUES (:id, :firstname, :lastname, 'unclaimed')
                """
            ),
            {
                "id": pid,
                "firstname": FIRST_NAMES[pid - 1],
                "lastname": LAST_NAMES[pid - 1],
            },
        )


def _insert_person_aliases(session: Any) -> None:
    """One alias per person with a middle initial."""
    initials = "ABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGHIJKLMNOPQRSTUVWXYZABCDEFGH"
    for pid in range(1, 61):
        first = FIRST_NAMES[pid - 1]
        last = LAST_NAMES[pid - 1]
        mi = initials[(pid - 1) % len(initials)]
        alias = f"{first} {mi}. {last}"
        session.execute(
            text(
                "INSERT INTO person_aliases (person_id, alias) VALUES (:pid, :alias)"
            ),
            {"pid": pid, "alias": alias},
        )


def _insert_person_affiliations(session: Any) -> None:
    """One chart_anchor affiliation per person."""
    lab_map = {
        (1, 15): 7,
        (16, 30): 8,
        (31, 45): 9,
        (46, 60): 10,
    }

    for pid in range(1, 61):
        org_id = None
        for (lo, hi), oid in lab_map.items():
            if lo <= pid <= hi:
                org_id = oid
                break
        assert org_id is not None

        rank = _rank_for(pid)
        aff_kind = _affiliation_kind_for(pid)

        if pid <= 50:
            starts = _dt(2019, 1, 1)
            ends = None
        else:
            starts = _dt(2010, 1, 1)
            retire_year = 2020 + ((pid - 51) % 4)
            ends = _dt(retire_year, 1, 1)

        session.execute(
            text(
                """
                INSERT INTO person_affiliations
                  (id, person_id, title, affiliation_kind, position_rank,
                   is_primary, starts_at, ends_at, verification_status)
                VALUES (:id, :pid, :title, :kind, :rank, TRUE, :starts, :ends, 'verified')
                """
            ),
            {
                "id": pid,
                "pid": pid,
                "title": f"{rank.replace('_', ' ').title()}",
                "kind": aff_kind,
                "rank": rank,
                "starts": starts,
                "ends": ends,
            },
        )


def _insert_affiliation_org_assignments(session: Any) -> None:
    """Map each affiliation to a chart_anchor org assignment."""
    lab_map = {
        (1, 15): 7,
        (16, 30): 8,
        (31, 45): 9,
        (46, 60): 10,
    }
    for pid in range(1, 61):
        org_id = None
        for (lo, hi), oid in lab_map.items():
            if lo <= pid <= hi:
                org_id = oid
                break
        session.execute(
            text(
                """
                INSERT INTO affiliation_org_assignments
                  (affiliation_id, organization_id, assignment_type)
                VALUES (:aff_id, :org_id, 'chart_anchor')
                """
            ),
            {"aff_id": pid, "org_id": org_id},
        )


# ---------------------------------------------------------------------------
# Publication author tuples (deterministic, no RNG)
# ---------------------------------------------------------------------------


def _build_paper_authors() -> list[tuple[int, ...]]:
    """Return a list of 180 author-id tuples, one per paper.

    Designed so the full coauthor graph is mostly connected (~5-8 components)
    to allow the disparity filter backbone to produce 6-10 leiden clusters.
    """
    papers: list[tuple[int, ...]] = []

    # --- Pair A: persons 1 & 2 share 10 papers ---
    for _ in range(8):
        papers.append((1, 2))

    # --- Pair B: persons 3 & 4 share 6 papers ---
    for _ in range(6):
        papers.append((3, 4))

    # --- Clique 1 (finance, 6-10): dominant pair (6,7), weight ~33 ---
    papers.append((6, 7, 8, 9, 10))          # 1 all-5
    for _ in range(32):
        papers.append((6, 7))                 # 32 dominant-pair-only

    # --- Clique 2 (macro, 11-15): dominant pair (11,12), weight ~33 ---
    papers.append((11, 12, 13, 14, 15))
    for _ in range(32):
        papers.append((11, 12))

    # --- Clique 3 (labor, 16-20): dominant pair (16,17), weight ~32 ---
    papers.append((16, 17, 18, 19, 20))
    for _ in range(31):
        papers.append((16, 17))

    # --- Bridge: person 21 cross-links clique-1 and clique-2 (8 papers) ---
    for i in range(4):
        papers.append((21, 6 + i))
    for i in range(4):
        papers.append((21, 11 + i))

    # --- Hub: person 5 with 7 coauthors (32-38), 8 papers ---
    hub_coauthors = list(range(32, 39))
    for i in range(8):
        coauthor = hub_coauthors[i % 7]
        papers.append((5, coauthor))

    # --- Cross-group bridges: connect cliques and pairs together ---
    # These ensure the full graph has at most ~8 components.
    cross_links = [
        (1, 6), (1, 11), (3, 16),         # pairs -> cliques
        (2, 8), (4, 18),                   # more cross-links
        (8, 13), (12, 7),                  # clique 1 <-> clique 2
        (10, 15), (19, 14),                # more inter-clique
        (5, 21), (5, 1),                   # hub <-> bridge, hub <-> pair A
        (21, 16),                          # bridge <-> clique 3
        (27, 6), (27, 11),                 # person 27 links cliques 1,2
    ]
    for a, b in cross_links:
        papers.append((a, b))

    # --- Weak-tie isolates: persons 51-60 get 1 connection each ---
    # so they are not degree-0 (which would force extra clusters).
    for i, pid in enumerate(range(51, 61)):
        anchor = (i % 5) + 1               # connect to persons 1-5
        papers.append((pid, anchor))

    # --- Multi-author papers: 6 papers, k=4..6, connecting groups ---
    multi_groups = [
        (1, 2, 6, 7),                     # pair A + clique 1 dominant
        (3, 4, 11, 12),                    # pair B + clique 2 dominant
        (5, 21, 16, 17),                   # hub + bridge + clique 3 dominant
        (27, 28, 29, 30, 31),              # cross-link people together
        (6, 11, 16, 32, 33),              # one from each clique + hub people
        (8, 13, 18, 34, 35),              # more inter-group
    ]
    for g in multi_groups:
        papers.append(g)

    # --- Fill: remaining papers to reach 180 ---
    fill_pool = list(range(1, 61))
    needed = 180 - len(papers)
    for i in range(needed):
        k = 3 + (i % 3)                    # 3-5 author papers
        start = (i * 11) % len(fill_pool)
        group = []
        for j in range(k):
            idx = (start + j * 7) % len(fill_pool)
            group.append(fill_pool[idx])
        uniq = list(dict.fromkeys(group))
        uniq.sort()
        papers.append(tuple(uniq))

    assert len(papers) == 180, f"Expected 180 papers, got {len(papers)}"
    return papers


PAPER_AUTHORS: list[tuple[int, ...]] = []


def _get_paper_authors() -> list[tuple[int, ...]]:
    global PAPER_AUTHORS
    if not PAPER_AUTHORS:
        PAPER_AUTHORS = _build_paper_authors()
    return PAPER_AUTHORS


def _insert_publications(session: Any) -> None:
    papers = _get_paper_authors()
    for i, authors in enumerate(papers):
        pub_id = i + 1
        year = 2012 + ((pub_id - 1) % 15)
        cited = (pub_id * 7 + 23) % 200
        n_authors = len(authors)
        if pub_id <= 18:
            area = "Macroeconomic Dynamics"
        elif pub_id <= 32:
            area = "Tax Policy Analysis"
        elif pub_id <= 40:
            area = "Corporate Finance"
        elif pub_id <= 48:
            area = "Monetary Theory"
        elif pub_id <= 56:
            area = "Labor Market Structure"
        elif pub_id <= 64:
            area = "Cross-Field Perspectives"
        elif pub_id <= 94:
            area = "Collaborative Research"
        elif pub_id <= 124:
            area = "Empirical Methods"
        else:
            area = "Economic Inquiry"

        if n_authors == 1:
            auth_str = FIRST_NAMES[authors[0] - 1]
        elif n_authors == 2:
            auth_str = f"{FIRST_NAMES[authors[0]-1]} and {FIRST_NAMES[authors[1]-1]}"
        else:
            auth_str = f"{FIRST_NAMES[authors[0]-1]}, {FIRST_NAMES[authors[1]-1]}, and colleagues"

        title = f"Demo paper {pub_id} of {auth_str} on {area}"

        month = 1 + (pub_id % 12)
        day = 1 + (pub_id % 28)
        pub_date = date(year, month, day)

        session.execute(
            text(
                """
                INSERT INTO publications
                  (id, title, publication_year, publication_date, cited_by_count)
                VALUES (:id, :title, :year, :date, :cited)
                """
            ),
            {
                "id": pub_id,
                "title": title,
                "year": year,
                "date": pub_date,
                "cited": cited,
            },
        )


def _insert_publication_authors(session: Any) -> None:
    papers = _get_paper_authors()
    for pub_id_minus_1, authors in enumerate(papers):
        pub_id = pub_id_minus_1 + 1
        for pos, person_id in enumerate(authors, start=1):
            session.execute(
                text(
                    """
                    INSERT INTO publication_authors
                      (publication_id, person_id, author_position, is_corresponding)
                    VALUES (:pub, :pid, :pos, :corr)
                    """
                ),
                {
                    "pub": pub_id,
                    "pid": person_id,
                    "pos": pos,
                    "corr": pos == 1,
                },
            )


# ---------------------------------------------------------------------------
# Concepts (12, level 1 and 2)
# ---------------------------------------------------------------------------

CONCEPT_DEFS: list[dict[str, Any]] = [
    {"id": 1, "display_name": "Economics", "parent_id": None, "level": 1},
    {"id": 2, "display_name": "Finance", "parent_id": None, "level": 1},
    {"id": 3, "display_name": "Business", "parent_id": None, "level": 1},
    {"id": 4, "display_name": "Statistics", "parent_id": None, "level": 1},
    {"id": 5, "display_name": "Macroeconomics", "parent_id": 1, "level": 2},
    {"id": 6, "display_name": "Monetary Policy", "parent_id": 1, "level": 2},
    {"id": 7, "display_name": "Corporate Finance", "parent_id": 2, "level": 2},
    {"id": 8, "display_name": "Asset Pricing", "parent_id": 2, "level": 2},
    {"id": 9, "display_name": "Organizational Behavior", "parent_id": 3, "level": 2},
    {"id": 10, "display_name": "Marketing", "parent_id": 3, "level": 2},
    {"id": 11, "display_name": "Econometrics", "parent_id": 4, "level": 2},
    {"id": 12, "display_name": "Causal Inference", "parent_id": 4, "level": 2},
]


def _insert_concepts(session: Any) -> None:
    for c in CONCEPT_DEFS:
        session.execute(
            text(
                """
                INSERT INTO concepts (id, display_name, parent_id, level)
                VALUES (:id, :display_name, :parent_id, :level)
                """
            ),
            c,
        )


def _person_concept_scores(pid: int) -> list[tuple[int, float]]:
    """Return (concept_id, score) pairs for a person, best 8."""
    if pid in (6, 7, 8, 9, 10):
        base = [
            (2, 0.95), (7, 0.88), (8, 0.85), (1, 0.62),
            (3, 0.55), (11, 0.48), (5, 0.35), (12, 0.28),
        ]
    elif pid in (11, 12, 13, 14, 15):
        base = [
            (1, 0.92), (5, 0.89), (6, 0.85), (4, 0.58),
            (11, 0.52), (12, 0.45), (2, 0.30), (3, 0.25),
        ]
    elif pid in (16, 17, 18, 19, 20):
        base = [
            (1, 0.90), (6, 0.82), (5, 0.78), (12, 0.65),
            (4, 0.55), (11, 0.50), (3, 0.35), (7, 0.22),
        ]
    elif pid in (1, 2, 3, 4):
        base = [
            (1, 0.90), (5, 0.85), (6, 0.80), (12, 0.62),
            (11, 0.55), (4, 0.48), (2, 0.32), (8, 0.25),
        ]
    elif 32 <= pid <= 45:
        base = [
            (2, 0.88), (7, 0.82), (3, 0.78), (8, 0.70),
            (9, 0.60), (1, 0.50), (11, 0.40), (10, 0.30),
        ]
    elif 51 <= pid <= 60:
        base = [
            (4, 0.90), (11, 0.85), (12, 0.80), (1, 0.65),
            (6, 0.55), (5, 0.50), (9, 0.40), (2, 0.30),
        ]
    else:
        base = [
            (1, 0.85), (5, 0.78), (11, 0.72), (12, 0.65),
            (4, 0.55), (3, 0.48), (6, 0.38), (2, 0.30),
        ]

    result = []
    for idx, (cid, base_score) in enumerate(base):
        adjusted = base_score + ((pid * 7 + cid * 3) % 10 - 5) * 0.01
        adjusted = max(0.2, min(0.95, adjusted))
        result.append((cid, round(adjusted, 4)))
    return result


def _insert_person_concepts(session: Any) -> None:
    for pid in range(1, 61):
        scores = _person_concept_scores(pid)
        for rank, (cid, score) in enumerate(scores, start=1):
            session.execute(
                text(
                    """
                    INSERT INTO person_concepts (person_id, concept_id, score, rank)
                    VALUES (:pid, :cid, :score, :rank)
                    """
                ),
                {"pid": pid, "cid": cid, "score": score, "rank": rank},
            )


def _pub_concept_ids(pub_id: int) -> list[tuple[int, float]]:
    """Return (concept_id, score) for a publication, 2-4 concepts."""
    n = 2 + (pub_id % 3)
    result = []
    for j in range(n):
        cid = ((pub_id * 3 + j * 7) % 12) + 1
        score = 0.3 + ((pub_id * 13 + j * 17) % 70) / 100.0
        score = round(score, 4)
        if cid not in [c for c, _ in result]:
            result.append((cid, score))
    return result


def _insert_publication_concepts(session: Any) -> None:
    for pub_id in range(1, 181):
        for cid, score in _pub_concept_ids(pub_id):
            session.execute(
                text(
                    """
                    INSERT INTO publication_concepts (publication_id, concept_id, score)
                    VALUES (:pub, :cid, :score)
                    """
                ),
                {"pub": pub_id, "cid": cid, "score": score},
            )


# ---------------------------------------------------------------------------
# Topics
# ---------------------------------------------------------------------------

TOPIC_DEFS: list[dict[str, Any]] = [
    {
        "openalex_topic_id": f"T{10001 + i}",
        "display_name": name,
        "subfield_name": sub,
        "field_name": field,
        "domain_name": dom,
        "level": 3,
    }
    for i, (name, sub, field, dom) in enumerate(
        [
            (
                "Macroeconomics - Monetary Policy",
                "Macroeconomics",
                "Economics, Econometrics and Finance",
                "Social Sciences",
            ),
            (
                "Corporate Finance - Capital Structure",
                "Finance",
                "Economics, Econometrics and Finance",
                "Social Sciences",
            ),
            (
                "Asset Pricing - Market Efficiency",
                "Finance",
                "Economics, Econometrics and Finance",
                "Social Sciences",
            ),
            (
                "Labor Economics - Wage Determination",
                "Labor Economics",
                "Economics, Econometrics and Finance",
                "Social Sciences",
            ),
            (
                "Organizational Behavior - Leadership",
                "Organizational Behavior",
                "Business, Management and Accounting",
                "Social Sciences",
            ),
            (
                "Marketing - Consumer Behavior",
                "Marketing",
                "Business, Management and Accounting",
                "Social Sciences",
            ),
            (
                "Econometrics - Causal Methods",
                "Econometrics",
                "Economics, Econometrics and Finance",
                "Social Sciences",
            ),
            (
                "Decision Theory - Behavioral Economics",
                "Decision Sciences",
                "Decision Sciences",
                "Social Sciences",
            ),
            (
                "Social Psychology - Group Dynamics",
                "Social Psychology",
                "Psychology",
                "Social Sciences",
            ),
            (
                "Public Economics - Taxation",
                "Public Economics",
                "Economics, Econometrics and Finance",
                "Social Sciences",
            ),
        ]
    )
]


def _insert_topics(session: Any) -> None:
    for t in TOPIC_DEFS:
        session.execute(
            text(
                """
                INSERT INTO topics
                  (openalex_topic_id, display_name, subfield_name, field_name,
                   domain_name, level)
                VALUES (:openalex_topic_id, :display_name, :subfield_name,
                        :field_name, :domain_name, :level)
                """
            ),
            t,
        )


def _pub_topic_ids(pub_id: int) -> list[tuple[str, float, bool]]:
    """Return (topic_id, score, is_primary) for a publication, 2-3 topics."""
    n = 2 + (pub_id % 2)
    result = []
    for k in range(n):
        tidx = (pub_id + k) % 10
        topic_id = f"T{10001 + tidx}"
        score = 0.3 + ((pub_id * 11 + k * 13) % 60) / 100.0
        score = round(max(0.3, min(0.9, score)), 4)
        if topic_id not in [t for t, _, _ in result]:
            is_primary = k == 0
            result.append((topic_id, score, is_primary))
    return result


def _insert_publication_topics(session: Any) -> None:
    for pub_id in range(1, 181):
        for tid, score, is_prim in _pub_topic_ids(pub_id):
            session.execute(
                text(
                    """
                    INSERT INTO publication_topics
                      (publication_id, topic_id, score, is_primary)
                    VALUES (:pub, :tid, :score, :is_primary)
                    """
                ),
                {"pub": pub_id, "tid": tid, "score": score, "is_primary": is_prim},
            )


def _insert_person_topics(session: Any) -> None:
    """Aggregate person_topics from publication_authors + publication_topics."""
    session.execute(
        text(
            """
            INSERT INTO person_topics (person_id, topic_id, score, works_count)
            SELECT
              pa.person_id,
              pt.topic_id,
              ROUND(AVG(pt.score)::numeric, 4) AS score,
              COUNT(DISTINCT pa.publication_id) AS works_count
            FROM publication_authors pa
            JOIN publication_topics pt ON pt.publication_id = pa.publication_id
            GROUP BY pa.person_id, pt.topic_id
            """
        )
    )


# ---------------------------------------------------------------------------
# Materialized view refresh
# ---------------------------------------------------------------------------


def _refresh_matviews(session: Any) -> None:
    matviews = [
        "person_coauthor_edges",
        "person_anchor",
        "org_current_roster",
        "org_tree_current",
    ]
    for mv in matviews:
        try:
            session.execute(text(f"REFRESH MATERIALIZED VIEW {mv}"))
            print(f"  [OK] {mv} refreshed")
        except Exception as exc:
            print(f"  [FAIL] {mv}: {exc}")


# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------


def _print_summary(session: Any) -> None:
    queries = [
        ("people", "SELECT count(*) FROM people"),
        ("publications", "SELECT count(*) FROM publications"),
        ("publication_authors", "SELECT count(*) FROM publication_authors"),
        ("person_coauthor_edges", "SELECT count(*) FROM person_coauthor_edges"),
        ("person_topics", "SELECT count(*) FROM person_topics"),
        ("topics", "SELECT count(*) FROM topics"),
        ("concepts", "SELECT count(*) FROM concepts"),
        ("person_concepts", "SELECT count(*) FROM person_concepts"),
        ("publication_concepts", "SELECT count(*) FROM publication_concepts"),
        ("publication_topics", "SELECT count(*) FROM publication_topics"),
        ("organizations", "SELECT count(*) FROM organizations"),
        ("org_relationships", "SELECT count(*) FROM org_relationships"),
        ("person_affiliations", "SELECT count(*) FROM person_affiliations"),
        ("affiliation_org_assignments", "SELECT count(*) FROM affiliation_org_assignments"),
        ("person_anchor", "SELECT count(*) FROM person_anchor"),
        ("org_current_roster", "SELECT count(*) FROM org_current_roster"),
        ("org_tree_current", "SELECT count(*) FROM org_tree_current"),
    ]
    print("\n--- Seed Summary ---")
    for label, q in queries:
        try:
            cnt = session.execute(text(q)).scalar()
            print(f"  {label}: {cnt}")
        except Exception as exc:
            print(f"  {label}: ERROR - {exc}")
    print("---------------------")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    session = _SessionLocal()
    try:
        print("Deleting existing rows ...")
        _delete_all(session)
        session.flush()

        print("Resetting sequences ...")
        _reset_sequences(session)
        session.flush()

        print("Inserting organizations ...")
        _insert_organizations(session)

        print("Inserting org_relationships ...")
        _insert_org_relationships(session)

        print("Inserting people ...")
        _insert_people(session)

        print("Inserting person_aliases ...")
        _insert_person_aliases(session)

        print("Inserting person_affiliations ...")
        _insert_person_affiliations(session)

        print("Inserting affiliation_org_assignments ...")
        _insert_affiliation_org_assignments(session)

        print("Inserting publications ...")
        _insert_publications(session)

        print("Inserting publication_authors ...")
        _insert_publication_authors(session)

        print("Inserting concepts ...")
        _insert_concepts(session)

        print("Inserting person_concepts ...")
        _insert_person_concepts(session)

        print("Inserting publication_concepts ...")
        _insert_publication_concepts(session)

        print("Inserting topics ...")
        _insert_topics(session)

        print("Inserting publication_topics ...")
        _insert_publication_topics(session)

        print("Aggregating person_topics ...")
        _insert_person_topics(session)

        print("Refreshing materialized views ...")
        _refresh_matviews(session)

        session.commit()
        print("Commit OK.")

        # The demo dataset needs its own atlas projections: the projection
        # resolvers read person_projections_2d, which only build_atlas fills.
        from scripts.embed.build_atlas import main as build_atlas_main

        build_atlas_main(["--view", "both"])

        _print_summary(session)

    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


if __name__ == "__main__":
    main()
