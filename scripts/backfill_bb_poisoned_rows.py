#!/usr/bin/env python3
"""One-shot backfill: clear bb_fetched_at for poisoned rows.

v1.0.9.3: v1.0.5.25 had a bug where transient API failures wrote
bb_fetched_at but kept all bb_* fields NULL.  v1.0.9.2 fixed new
failures but doesn't migrate historical rows.  This script clears
bb_fetched_at for any bestblogs* row that has bb_fetched_at set
but all 5 bb_* fields NULL — these are the poisoned rows that
need to be retried on the next share.

Usage:
    python3 scripts/backfill_bb_poisoned_rows.py            # dry-run (default)
    python3 scripts/backfill_bb_poisoned_rows.py --commit   # apply
    python3 scripts/backfill_bb_poisoned_rows.py --db PATH  # custom DB
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = REPO_ROOT / "data" / "news.db"


def find_poisoned_rows(conn: sqlite3.Connection) -> list[tuple[str, str, str]]:
    """Return list of (id, source, bb_fetched_at) for poisoned rows.

    A "poisoned" row matches ALL of:
      - source is bestblogs or any bestblog-* sub-source
      - bb_fetched_at is set (we tried to fetch and recorded completion)
      - all 5 user-visible bb_* fields (bb_score, bb_summary,
        bb_main_points, bb_key_quotes, bb_tags) are NULL — the
        fetch hit a transient error and recorded fetch time but
        never got actual data
    """
    cur = conn.execute(
        """
        SELECT id, source, bb_fetched_at FROM articles
        WHERE (source = 'bestblogs' OR source LIKE 'bestblog-%')
          AND bb_fetched_at IS NOT NULL
          AND bb_score IS NULL
          AND bb_summary IS NULL
          AND bb_main_points IS NULL
          AND bb_key_quotes IS NULL
          AND bb_tags IS NULL
        ORDER BY bb_fetched_at DESC
        """
    )
    return [(r[0], r[1], r[2]) for r in cur.fetchall()]


def unpoison(conn: sqlite3.Connection, dry_run: bool = True) -> int:
    rows = find_poisoned_rows(conn)
    if not rows:
        print("[backfill] no poisoned rows found")
        return 0
    print(f"[backfill] found {len(rows)} poisoned row(s):")
    for aid, source, fetched_at in rows:
        print(f"  - {aid}  source={source}  bb_fetched_at={fetched_at}")
    if dry_run:
        print("[backfill] DRY RUN — no changes made. Re-run with --commit to apply.")
        return 0
    ids = [r[0] for r in rows]
    placeholders = ",".join("?" * len(ids))
    cur = conn.execute(
        f"UPDATE articles SET bb_fetched_at = NULL WHERE id IN ({placeholders})",
        ids,
    )
    conn.commit()
    print(f"[backfill] cleared bb_fetched_at for {cur.rowcount} row(s)")
    return cur.rowcount


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Clear bb_fetched_at for poisoned bestblogs* rows (v1.0.9.3)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--db", default=str(DEFAULT_DB),
                        help=f"Path to sqlite DB (default: {DEFAULT_DB})")
    parser.add_argument("--dry-run", action="store_true", default=True,
                        help="(default) Show what would change, don't write")
    parser.add_argument("--commit", action="store_true",
                        help="Apply the backfill (clears bb_fetched_at)")
    args = parser.parse_args()
    db = Path(args.db)
    if not db.exists():
        print(f"[backfill] DB not found: {db}", file=sys.stderr)
        return 1
    # Default to dry-run unless --commit explicitly given
    dry_run = not args.commit
    conn = sqlite3.connect(str(db))
    try:
        unpoison(conn, dry_run=dry_run)
    finally:
        conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
