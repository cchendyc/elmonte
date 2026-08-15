#!/usr/bin/env python3
"""Restore the original repo seed data (``db/seed.sql``) into the current schema.

The repo shipped with ``db/seed.sql`` (a synthetic prototype dataset:
9 universities, 12 people, concepts, awards, grants, evidence, ...) but its
primary keys are **text slugs** (``'inst-uoft'``, ``'person-a'``) while
the current schema uses BIGINT identity ids — the old file can't be loaded
as-is.

This script parses ``db/seed.sql``, assigns deterministic integer ids in file
order, adapts columns (validity ranges are computed by the schema from
``starts_at``/``ends_at``), derives the newer semantic tables the old seed
predates (``topics`` from concepts, ``person_topics``, ``publication_topics``),
and refreshes the materialized views the app reads (``person_anchor``,
``person_coauthor_edges``, ``org_current_roster``, ``org_tree_current``).

**Destructive:** truncates every data table first, so re-runs are safe.
Run ``python3 -m scripts.embed.build_atlas --view both`` afterwards to build
the atlas projection.  The synthetic demo dataset can be restored any time
with ``python3 -m scripts.db.seed_demo --reset``.

Usage::

    python3 -m scripts.db.restore_legacy_seed [--dry-run]
"""

from __future__ import annotations

import argparse
import sys
from collections import defaultdict
from decimal import Decimal
from pathlib import Path
from typing import Any

from sqlalchemy import text

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SEED_PATH = _REPO_ROOT / "db" / "seed.sql"

# Every data table, in any order (no FK constraints in the schema).  Identity
# sequences are reset so explicit ids 1..N line up with the next auto value.
_TRUNCATE_TABLES = [
    "evidence",
    "external_identifiers",
    "source_snapshots",
    "grant_participants",
    "grants",
    "publication_citations",
    "publication_author_affiliations",
    "publication_authors",
    "publication_concepts",
    "publications",
    "person_relationships",
    "affiliation_org_assignments",
    "person_affiliations",
    "person_awards",
    "awards",
    "person_concepts",
    "person_aliases",
    "people",
    "concepts",
    "org_relationships",
    "organizations",
    "topics",
    "person_topics",
    "publication_topics",
    "embedding_runs",
    "person_projections_2d",
    "projection_clusters",
    "projection_cluster_edges",
]

_MATERIALIZED_VIEWS = [
    "person_anchor",
    "person_coauthor_edges",
    "org_current_roster",
    "org_tree_current",
]

# ---------------------------------------------------------------------------
# minimal SQL INSERT parser (db/seed.sql is a fixed, small, hand-written file)
# ---------------------------------------------------------------------------


def _split_values(body: str) -> list[str]:
    """Split a ``VALUES`` body into row strings, respecting quoted strings.

    Each captured row starts *after* its opening paren and ends before its
    closing paren.  (Values in this file never contain nested parens outside
    quoted strings, so a simple depth counter suffices.)
    """
    rows: list[str] = []
    start = 0
    in_str = False
    depth = 0
    need_start = True
    for i, ch in enumerate(body):
        if ch == "'":
            in_str = not in_str
        elif ch == "(" and not in_str:
            if depth == 0 and need_start:
                start = i + 1  # skip this row's opening paren
                need_start = False
            depth += 1
        elif ch == ")" and not in_str:
            depth -= 1
            if depth == 0:
                rows.append(body[start:i])
                need_start = True
    return [r for r in rows if r.strip()]


def _split_columns(row: str) -> list[str]:
    parts: list[str] = []
    cur = ""
    in_str = False
    for ch in row:
        if ch == "'":
            in_str = not in_str
            cur += ch
        elif ch == "," and not in_str:
            parts.append(cur.strip())
            cur = ""
        else:
            cur += ch
    if cur.strip():
        parts.append(cur.strip())
    return parts


def _to_value(raw: str) -> Any:
    raw = raw.strip()
    if raw == "NULL":
        return None
    if raw == "TRUE":
        return True
    if raw == "FALSE":
        return False
    if raw.startswith("'") and raw.endswith("'"):
        return raw[1:-1].replace("''", "'")
    try:
        return int(raw)
    except ValueError:
        try:
            return Decimal(raw)
        except Exception as exc:
            raise ValueError(f"cannot parse seed value: {raw!r}") from exc


def parse_seed(path: Path) -> dict[str, list[dict[str, Any]]]:
    """Parse seed.sql into ``{table: [{column: value, ...}, ...]}``."""
    statements = [s for s in path.read_text(encoding="utf-8").split(";") if "INSERT INTO" in s]
    tables: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for stmt in statements:
        head, _, body = stmt.partition("VALUES")
        insert_pos = head.find("INSERT INTO")
        if insert_pos == -1:  # defensive — filter above already guarantees one
            continue
        table, cols_str = head[insert_pos:].removeprefix("INSERT INTO").split("(", 1)
        columns = [c.strip() for c in cols_str.rsplit(")", 1)[0].split(",")]
        for row in _split_values(body):
            values = _split_columns(row)
            if len(values) != len(columns):
                raise ValueError(
                    f"{table}: row has {len(values)} values for {len(columns)} columns"
                )
            tables[table.strip()].append(
                {col: _to_value(val) for col, val in zip(columns, values)}
            )
    return dict(tables)


def _assign_ids(rows: list[dict[str, Any]]) -> dict[str, int]:
    """Deterministic text-id -> int mapping in file order."""
    mapping: dict[str, int] = {}
    for i, row in enumerate(rows, start=1):
        mapping[str(row["id"])] = i
    return mapping


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------


def _topic_lineage(
    concepts: list[dict[str, Any]], concept_ids: dict[str, int]
) -> dict[str, tuple[str | None, str | None]]:
    """For each concept, resolve (subfield_name, field_name) via the parent chain."""
    name_by_slug = {str(c["id"]): str(c["display_name"]) for c in concepts}
    parent_by_slug = {
        str(c["id"]): str(c["parent_id"]) if c.get("parent_id") else None
        for c in concepts
    }
    lineage: dict[str, tuple[str | None, str | None]] = {}
    for c in concepts:
        slug = str(c["id"])
        parent = parent_by_slug[slug]
        subfield = name_by_slug[parent] if parent else None
        field = None
        cursor = parent
        while cursor:
            field = name_by_slug[cursor]
            cursor = parent_by_slug.get(cursor)
        if field is None:
            # A top-level concept is its own field (so cluster labels never
            # degrade to "Unknown" for people whose only topics are roots).
            field = name_by_slug[slug]
        lineage[slug] = (subfield, field)
    return lineage


def restore(dry_run: bool = False) -> None:
    seed = parse_seed(_SEED_PATH)
    print(f"Parsed {_SEED_PATH.name}: {len(seed)} tables")

    orgs = seed["organizations"]
    org_ids = _assign_ids(orgs)
    rels = seed["org_relationships"]
    rel_ids = _assign_ids(rels)
    concepts = seed["concepts"]
    concept_ids = _assign_ids(concepts)
    people = seed["people"]
    person_ids = _assign_ids(people)
    affils = seed["person_affiliations"]
    affil_ids = _assign_ids(affils)
    prels = seed["person_relationships"]
    prel_ids = _assign_ids(prels)
    pubs = seed["publications"]
    pub_ids = _assign_ids(pubs)
    awards = seed["awards"]
    award_ids = _assign_ids(awards)
    pawards = seed["person_awards"]
    paward_ids = _assign_ids(pawards)
    grants = seed["grants"]
    grant_ids = _assign_ids(grants)
    snaps = seed["source_snapshots"]
    snap_ids = _assign_ids(snaps)
    extids = seed["external_identifiers"]
    evidence = seed["evidence"]

    lineage = _topic_lineage(seed["concepts"], concept_ids)

    print(
        "Plan:",
        len(orgs), "orgs,",
        len(people), "people,",
        len(concepts), "concepts/topics,",
        len(affils), "affiliations,",
        len(pubs), "publications,",
        len(prels), "person relationships,",
        len(grants), "grants,",
        len(extids), "external ids,",
        len(evidence), "evidence rows",
    )
    if dry_run:
        print("Dry run — nothing written.")
        return

    with _SessionLocal() as s:
        # ----- wipe --------------------------------------------------------
        s.execute(text(f"TRUNCATE TABLE {', '.join(_TRUNCATE_TABLES)} RESTART IDENTITY"))

        # ----- organizations ------------------------------------------------
        for row in orgs:
            s.execute(
                text(
                    "INSERT INTO organizations (id, name, short_name, country, kind, description) "
                    "VALUES (:id, :name, :short_name, :country, :kind, :description)"
                ),
                {
                    "id": org_ids[str(row["id"])],
                    "name": row["name"],
                    "short_name": row["short_name"],
                    "country": row["country"],
                    "kind": row["kind"],
                    "description": row["description"],
                },
            )

        # ----- org hierarchy -------------------------------------------------
        for row in rels:
            s.execute(
                text(
                    "INSERT INTO org_relationships "
                    "(id, child_org_id, parent_org_id, relationship_type, starts_at, verification_status) "
                    "VALUES (:id, :child, :parent, :type, :starts_at, :verified)"
                ),
                {
                    "id": rel_ids[str(row["id"])],
                    "child": org_ids[str(row["child_org_id"])],
                    "parent": org_ids[str(row["parent_org_id"])],
                    "type": row["relationship_type"],
                    "starts_at": row["starts_at"],
                    "verified": row["verification_status"],
                },
            )

        # ----- concepts + derived topics --------------------------------------
        for row in seed["concepts"]:
            cid = concept_ids[str(row["id"])]
            s.execute(
                text(
                    "INSERT INTO concepts (id, display_name, parent_id, level) "
                    "VALUES (:id, :name, :parent, :level)"
                ),
                {
                    "id": cid,
                    "name": row["display_name"],
                    "parent": concept_ids[str(row["parent_id"])] if row.get("parent_id") else None,
                    "level": row["level"],
                },
            )
            subfield, field = lineage[str(row["id"])]
            s.execute(
                text(
                    "INSERT INTO topics (openalex_topic_id, display_name, subfield_name, field_name, level) "
                    "VALUES (:tid, :name, :subfield, :field, :level)"
                ),
                {
                    "tid": f"legacy:{cid:03d}",
                    "name": row["display_name"],
                    "subfield": subfield,
                    "field": field,
                    "level": row["level"],
                },
            )

        # ----- people ---------------------------------------------------------
        for row in people:
            s.execute(
                text(
                    "INSERT INTO people (id, firstname, middlename, lastname, biography, claimed_status) "
                    "VALUES (:id, :first, :middle, :last, :bio, :claimed)"
                ),
                {
                    "id": person_ids[str(row["id"])],
                    "first": row["firstname"],
                    "middle": row["middlename"],
                    "last": row["lastname"],
                    "bio": row["biography"],
                    "claimed": row["claimed_status"],
                },
            )

        for row in seed["person_aliases"]:
            s.execute(
                text("INSERT INTO person_aliases (person_id, alias) VALUES (:pid, :alias)"),
                {"pid": person_ids[str(row["person_id"])], "alias": row["alias"]},
            )

        # ----- person concepts -> person_topics -------------------------------
        for row in seed["person_concepts"]:
            cid = concept_ids[str(row["concept_id"])]
            s.execute(
                text(
                    "INSERT INTO person_concepts (person_id, concept_id, rank) "
                    "VALUES (:pid, :cid, :rank)"
                ),
                {
                    "pid": person_ids[str(row["person_id"])],
                    "cid": cid,
                    "rank": row["rank"],
                },
            )
            s.execute(
                text(
                    "INSERT INTO person_topics (person_id, topic_id, score, works_count) "
                    "VALUES (:pid, :tid, :score, 0)"
                ),
                {
                    "pid": person_ids[str(row["person_id"])],
                    "tid": f"legacy:{cid:03d}",
                    # rank 1 = strongest affinity -> 1.0, rank 2 -> 0.5
                    "score": 1.0 / float(row["rank"]) if row["rank"] else 1.0,
                },
            )

        # ----- awards ----------------------------------------------------------
        for row in awards:
            s.execute(
                text(
                    "INSERT INTO awards (id, name, awarding_org_id) VALUES (:id, :name, :org)"
                ),
                {
                    "id": award_ids[str(row["id"])],
                    "name": row["name"],
                    "org": org_ids[str(row["awarding_org_id"])],
                },
            )
        for row in pawards:
            s.execute(
                text(
                    "INSERT INTO person_awards "
                    "(id, person_id, award_id, awarded_at, verification_status) "
                    "VALUES (:id, :pid, :aid, :at, :verified)"
                ),
                {
                    "id": paward_ids[str(row["id"])],
                    "pid": person_ids[str(row["person_id"])],
                    "aid": award_ids[str(row["award_id"])],
                    "at": row["awarded_at"],
                    "verified": row["verification_status"],
                },
            )

        # ----- affiliations -----------------------------------------------------
        for row in affils:
            s.execute(
                text(
                    "INSERT INTO person_affiliations "
                    "(id, person_id, title, affiliation_kind, position_rank, is_primary, starts_at, verification_status) "
                    "VALUES (:id, :pid, :title, :kind, :rank, :primary, :starts_at, :verified)"
                ),
                {
                    "id": affil_ids[str(row["id"])],
                    "pid": person_ids[str(row["person_id"])],
                    "title": row["title"],
                    "kind": row["affiliation_kind"],
                    "rank": row["position_rank"],
                    "primary": row["is_primary"],
                    "starts_at": row["starts_at"],
                    "verified": row["verification_status"],
                },
            )
        for row in seed["affiliation_org_assignments"]:
            s.execute(
                text(
                    "INSERT INTO affiliation_org_assignments "
                    "(affiliation_id, organization_id, assignment_type) "
                    "VALUES (:aid, :oid, :type)"
                ),
                {
                    "aid": affil_ids[str(row["affiliation_id"])],
                    "oid": org_ids[str(row["organization_id"])],
                    "type": row["assignment_type"],
                },
            )

        # ----- person relationships ---------------------------------------------
        for row in prels:
            s.execute(
                text(
                    "INSERT INTO person_relationships "
                    "(id, type, from_person_id, to_person_id, verification_status) "
                    "VALUES (:id, :type, :frm, :to, :verified)"
                ),
                {
                    "id": prel_ids[str(row["id"])],
                    "type": row["type"],
                    "frm": person_ids[str(row["from_person_id"])],
                    "to": person_ids[str(row["to_person_id"])],
                    "verified": row["verification_status"],
                },
            )

        # ----- publications ------------------------------------------------------
        for row in pubs:
            s.execute(
                text(
                    "INSERT INTO publications (id, title, publication_year) "
                    "VALUES (:id, :title, :year)"
                ),
                {
                    "id": pub_ids[str(row["id"])],
                    "title": row["title"],
                    "year": row["publication_year"],
                },
            )
        for row in seed["publication_concepts"]:
            cid = concept_ids[str(row["concept_id"])]
            s.execute(
                text(
                    "INSERT INTO publication_concepts (publication_id, concept_id) "
                    "VALUES (:pid, :cid)"
                ),
                {
                    "pid": pub_ids[str(row["publication_id"])],
                    "cid": cid,
                },
            )
            s.execute(
                text(
                    "INSERT INTO publication_topics (publication_id, topic_id, score, is_primary) "
                    "VALUES (:pid, :tid, 1.0, TRUE)"
                ),
                {
                    "pid": pub_ids[str(row["publication_id"])],
                    "tid": f"legacy:{cid:03d}",
                },
            )
        for row in seed["publication_authors"]:
            s.execute(
                text(
                    "INSERT INTO publication_authors "
                    "(publication_id, person_id, author_position) "
                    "VALUES (:pid, :person, :pos)"
                ),
                {
                    "pid": pub_ids[str(row["publication_id"])],
                    "person": person_ids[str(row["person_id"])],
                    "pos": row["author_position"],
                },
            )
        for row in seed["publication_author_affiliations"]:
            s.execute(
                text(
                    "INSERT INTO publication_author_affiliations "
                    "(publication_id, person_id, organization_id, verification_status) "
                    "VALUES (:pid, :person, :org, :verified)"
                ),
                {
                    "pid": pub_ids[str(row["publication_id"])],
                    "person": person_ids[str(row["person_id"])],
                    "org": org_ids[str(row["organization_id"])],
                    "verified": row["verification_status"],
                },
            )
        for row in seed["publication_citations"]:
            s.execute(
                text(
                    "INSERT INTO publication_citations (citing_publication_id, cited_publication_id) "
                    "VALUES (:citing, :cited)"
                ),
                {
                    "citing": pub_ids[str(row["citing_publication_id"])],
                    "cited": pub_ids[str(row["cited_publication_id"])],
                },
            )

        # ----- grants --------------------------------------------------------------
        for row in grants:
            s.execute(
                text(
                    "INSERT INTO grants "
                    "(id, title, funder_org_id, award_number, amount, currency, starts_at, ends_at, verification_status) "
                    "VALUES (:id, :title, :funder, :number, :amount, :currency, :starts_at, :ends_at, :verified)"
                ),
                {
                    "id": grant_ids[str(row["id"])],
                    "title": row["title"],
                    "funder": org_ids[str(row["funder_org_id"])],
                    "number": row["award_number"],
                    "amount": row["amount"],
                    "currency": row["currency"],
                    "starts_at": row["starts_at"],
                    "ends_at": row["ends_at"],
                    "verified": row["verification_status"],
                },
            )
        for row in seed["grant_participants"]:
            s.execute(
                text(
                    "INSERT INTO grant_participants "
                    "(grant_id, person_id, organization_id, role) "
                    "VALUES (:gid, :pid, :oid, :role)"
                ),
                {
                    "gid": grant_ids[str(row["grant_id"])],
                    "pid": person_ids[str(row["person_id"])],
                    "oid": org_ids[str(row["organization_id"])],
                    "role": row["role"],
                },
            )

        # ----- identifiers & evidence ------------------------------------------------
        for row in extids:
            params: dict[str, Any] = {
                "provider": row["provider"],
                "external_id": row["external_id"],
                "person_id": None,
                "publication_id": None,
            }
            if row.get("person_id"):
                params["person_id"] = person_ids[str(row["person_id"])]
            if row.get("publication_id"):
                params["publication_id"] = pub_ids[str(row["publication_id"])]
            s.execute(
                text(
                    "INSERT INTO external_identifiers (provider, external_id, person_id, publication_id) "
                    "VALUES (:provider, :external_id, :person_id, :publication_id)"
                ),
                params,
            )

        for row in seed["source_snapshots"]:
            s.execute(
                text(
                    "INSERT INTO source_snapshots "
                    "(id, source_url, source_kind, fetched_at, content_hash, http_status) "
                    "VALUES (:id, :url, :kind, :fetched_at, :hash, :status)"
                ),
                {
                    "id": snap_ids[str(row["id"])],
                    "url": row["source_url"],
                    "kind": row["source_kind"],
                    "fetched_at": row["fetched_at"],
                    "hash": row["content_hash"],
                    "status": row["http_status"],
                },
            )

        for row in evidence:
            params: dict[str, Any] = {
                "label": row["label"],
                "snapshot_id": snap_ids[str(row["snapshot_id"])],
                "affiliation_id": None,
                "person_relationship_id": None,
                "person_award_id": None,
                "grant_id": None,
            }
            subject_mappings = {
                "affiliation_id": affil_ids,
                "person_relationship_id": prel_ids,
                "person_award_id": paward_ids,
                "grant_id": grant_ids,
            }
            for col, mapping in subject_mappings.items():
                if row.get(col):
                    params[col] = mapping[str(row[col])]
            s.execute(
                text(
                    "INSERT INTO evidence (label, snapshot_id, affiliation_id, person_relationship_id, "
                    "person_award_id, grant_id) "
                    "VALUES (:label, :snapshot_id, :affiliation_id, :person_relationship_id, "
                    ":person_award_id, :grant_id)"
                ),
                params,
            )

        # ----- identity sequences ----------------------------------------------------
        # TRUNCATE ... RESTART IDENTITY resets sequences to 1, but explicit ids
        # (1..N in file order) do NOT advance them — without this, the next
        # auto-insert would collide with the max existing id.
        #
        # Enumerate owners through pg_depend (deptype 'a' = SERIAL, 'i' =
        # GENERATED ... AS IDENTITY) rather than information_schema's
        # column_default: identity columns report no nextval() default, so
        # the naive scan silently misses them and the first auto-insert then
        # collides with the highest explicit id.
        seq_rows = s.execute(
            text(
                """
                SELECT tbl.relname AS table_name, att.attname AS column_name
                FROM pg_class seq
                JOIN pg_depend d
                  ON d.objid = seq.oid
                 AND d.classid = 'pg_class'::regclass
                 AND d.deptype IN ('a', 'i')
                JOIN pg_class tbl ON tbl.oid = d.refobjid
                JOIN pg_attribute att
                  ON att.attrelid = tbl.oid AND att.attnum = d.refobjsubid
                WHERE seq.relkind = 'S'
                  AND tbl.relnamespace = 'public'::regnamespace
                """
            )
        ).mappings().all()
        for seq in seq_rows:
            s.execute(
                text(
                    "SELECT setval(pg_get_serial_sequence(:t, :c), "
                    "GREATEST((SELECT COALESCE(MAX({col}), 1) FROM {tbl}), 1))".format(
                        col=seq["column_name"], tbl=seq["table_name"]
                    )
                ),
                {"t": seq["table_name"], "c": seq["column_name"]},
            )

        # ----- materialized views ---------------------------------------------------
        for mv in _MATERIALIZED_VIEWS:
            s.execute(text(f"REFRESH MATERIALIZED VIEW {mv}"))

        s.commit()

        # ----- summary ----------------------------------------------------------------
        checks = {
            "organizations": "SELECT count(*) FROM organizations",
            "universities": "SELECT count(*) FROM organizations WHERE kind = 'university'",
            "people": "SELECT count(*) FROM people",
            "concepts": "SELECT count(*) FROM concepts",
            "topics": "SELECT count(*) FROM topics",
            "person_topics": "SELECT count(*) FROM person_topics",
            "publications": "SELECT count(*) FROM publications",
            "person_relationships": "SELECT count(*) FROM person_relationships",
            "coauthor_edges": "SELECT count(*) FROM person_coauthor_edges",
            "anchors": "SELECT count(*) FROM person_anchor",
            "roster": "SELECT count(*) FROM org_current_roster",
            "external_identifiers": "SELECT count(*) FROM external_identifiers",
            "evidence": "SELECT count(*) FROM evidence",
            "grants": "SELECT count(*) FROM grants",
        }
        for label, sql in checks.items():
            count = s.execute(text(sql)).scalar_one()
            print(f"  {label:22s} {count}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="parse + print plan, write nothing")
    args = parser.parse_args()
    restore(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
