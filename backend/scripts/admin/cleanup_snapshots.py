from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.orm import Session

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from api.deps import _SessionLocal

# Repo root (backend/scripts/admin/ -> parents[3]) — snapshot files physically
# live at repo-root/data/ingest/raw even when run from backend/.
RAW_DIR = Path(__file__).resolve().parents[3] / "data" / "ingest" / "raw"
REPO_ROOT = RAW_DIR.parents[1]


def _protected_snapshot_ids(session: Session) -> set[int]:
    """Return the set of snapshot_id values referenced by other tables."""
    protected: set[int] = set()
    for table in ("evidence", "external_identifiers"):
        rows = session.execute(
            text(
                f"SELECT DISTINCT snapshot_id FROM {table} WHERE snapshot_id IS NOT NULL"
            )
        ).scalars()
        protected.update(int(r) for r in rows)
    return protected


def _expired_snapshot_ids(session: Session) -> list[tuple[int, str, str | None]]:
    """Return (id, source_url, local_path) for expired snapshots."""
    rows = session.execute(
        text(
            """
            SELECT id, source_url, local_path
            FROM source_snapshots
            WHERE expires_at IS NOT NULL
              AND expires_at < now()
            ORDER BY expires_at
            """
        )
    ).mappings().all()
    return [(int(r["id"]), r["source_url"], r["local_path"]) for r in rows]


def _delete_snapshots(session: Session, snapshot_ids: list[int]) -> None:
    for sid in snapshot_ids:
        session.execute(
            text("DELETE FROM source_snapshots WHERE id = :i"), {"i": sid}
        )


def _remove_local_files(local_paths: list[str]) -> tuple[int, int]:
    removed = 0
    skipped = 0
    raw_dir = RAW_DIR.resolve()
    for lp in local_paths:
        if not lp:
            skipped += 1
            continue
        p = Path(lp)
        if not p.is_absolute():
            p = REPO_ROOT / p
        p = p.resolve()
        # Safety: only remove files inside the raw directory.
        try:
            p.relative_to(raw_dir)
        except ValueError:
            skipped += 1
            continue
        if p.exists() and p.is_file():
            p.unlink()
            removed += 1
        else:
            skipped += 1
    return removed, skipped


def run(*, dry_run: bool) -> None:
    with _SessionLocal() as session:
        protected = _protected_snapshot_ids(session)
        expired = _expired_snapshot_ids(session)

        safe: list[tuple[int, str, str | None]] = []
        preserved: list[tuple[int, str, str | None]] = []
        for sid, url, lp in expired:
            if sid in protected:
                preserved.append((sid, url, lp))
            else:
                safe.append((sid, url, lp))

        print(f"Expired snapshots found:          {len(expired)}")
        print(f"  Preserved (referenced):          {len(preserved)}")
        print(f"  Ready to remove:                  {len(safe)}")

        if preserved:
            print("\nPreserved (referenced by evidence/external_ids, will NOT delete):")
            for sid, url, _lp in preserved:
                print(f"  id={sid}  {url}")

        if safe:
            print(f"\n{'Would remove' if dry_run else 'Removing'} {len(safe)} snapshots:")
            for sid, url, lp in safe:
                print(f"  id={sid}  {url}")
                if lp:
                    print(f"           local: {lp}")

        if dry_run:
            print("\n[dry-run] No changes committed.")
            session.rollback()
            return

        if not safe:
            print("\nNothing to remove.")
            return

        # Remove local files BEFORE committing DB rows: if file deletion fails
        # the transaction is still open and will be rolled back on exception.
        local_paths = [lp for _sid, _url, lp in safe if lp]
        removed_files, skipped_files = _remove_local_files(local_paths)
        print(f"\nLocal files removed:  {removed_files}")
        print(f"Local files skipped:  {skipped_files}")

        ids_to_delete = [sid for sid, _url, _lp in safe]
        _delete_snapshots(session, ids_to_delete)
        session.commit()
        print(f"DB rows removed:      {len(ids_to_delete)}")


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(
        description="Remove expired source snapshots not referenced by evidence."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be removed without making changes.",
    )
    args = parser.parse_args(argv)

    run(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
