"""Auto-migrate: apply schema.sql and incremental migrations if schema_version is behind.

v1.0 is single-schema + incremental migrations: we always run the full schema
on top of whatever exists — the CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
guards make it idempotent.  The schema_version table tracks applied_at.
"""

from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .connection import get_db, get_db_path

logger = logging.getLogger(__name__)


_CURRENT_VERSION = 59  # + 059 bv_direction_guidance (sprint-73 direction research guidance)

# Migrations <= this version were applied by the old "max(schema_version)"
# rule, which did not record every intermediate row.  Never re-run them.
# Versions above it (042 landed on main while 043 was in development) may
# be missing even when MAX(schema_version) is already higher; those holes
# still apply.
_HOLE_FILL_AFTER = 41


def _rollback_if_needed(conn: sqlite3.Connection) -> None:
    """Leave the connection usable after a failed migration statement."""
    if getattr(conn, "in_transaction", False):
        try:
            conn.rollback()
        except sqlite3.Error:
            logger.exception("rollback after failed migration statement failed")


def current_version(conn: sqlite3.Connection) -> int:
    """Return the currently applied schema version (0 if schema_version table doesn't exist)."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    v = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
    return int(v)


def _run_sql_file(conn: sqlite3.Connection, path: Path) -> None:
    """Execute a .sql file, stripping -- comments so they don't produce empty statements.

    Splits on ``;`` and runs each statement individually so that a
    caught error (e.g. duplicate column from ALTER TABLE) doesn't
    abort subsequent statements.
    """
    raw = path.read_text(encoding="utf-8")
    # Cleanup migrations may target tables that a hand-built test database
    # never created.  The file opts in explicitly instead of us swallowing
    # "no such table" everywhere.
    allow_missing_tables = "allow-missing-tables" in raw
    sql = re.sub(r"--+.*", "", raw)
    # naive split on ";" doesn't handle BEGIN..END triggers correctly.
    # Try executescript first (handles triggers), fall back to per-stmt on failure.
    try:
        conn.executescript(sql)
        return
    except sqlite3.Error as exc:
        _rollback_if_needed(conn)
        msg = str(exc).lower()
        # If duplicate column (idempotent ALTER), still try per-stmt and skip
        if "duplicate column" not in msg:
            logger.debug("executescript failed (%s); falling back to per-stmt", exc)
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            # PRAGMA foreign_keys is a no-op inside a transaction, and the
            # implicit transaction a DML statement opened would swallow it.
            if (
                stmt.upper().startswith("PRAGMA")
                and getattr(conn, "in_transaction", False)
            ):
                conn.commit()
            conn.execute(stmt)
        except sqlite3.Error as exc:
            msg = str(exc).lower()
            if "duplicate column" in msg:
                pass
            elif allow_missing_tables and "no such table" in msg:
                logger.warning("skipping statement, table absent: %s", exc)
                pass
            else:
                _rollback_if_needed(conn)
                raise



def migration_should_apply(version: int, applied: int, recorded: set[int]) -> bool:
    """Whether a migration file still needs to run.

    Historical databases only stored the highest version, so versions
    <= 41 that have no row are treated as already applied.  A later
    file such as 042 must still run if 043 landed first.
    """
    if version in recorded:
        return False
    if version <= applied and version <= _HOLE_FILL_AFTER:
        return False
    return True


def _recorded_versions(conn: sqlite3.Connection) -> set[int]:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return set()
    return {
        int(r[0])
        for r in conn.execute("SELECT version FROM schema_version").fetchall()
    }


def migrate() -> None:
    """Run pending migrations.

    Applies schema.sql (v1 base schema) then any pending incremental migrations
    in migrations/ directory.  A post-041 file that is absent from
    schema_version still runs after a higher version has been recorded, so
    parallel migrations (042 developed beside 043) are not skipped.
    """
    migrations_dir = Path(__file__).parent / "migrations"
    conn = get_db()
    applied = current_version(conn)
    recorded = _recorded_versions(conn)

    pending: list[tuple[int, Path]] = []
    for mig_file in sorted(migrations_dir.glob("*.sql")):
        mig_name = mig_file.stem
        try:
            mig_version = int(mig_name.split("_")[0])
        except (ValueError, IndexError):
            print(f"  Skipping {mig_file.name} (cannot parse version prefix)")
            continue
        if migration_should_apply(mig_version, applied, recorded):
            pending.append((mig_version, mig_file))

    if not pending and applied >= _CURRENT_VERSION:
        print(f"DB schema already at version {applied}, nothing to migrate.")
        return

    schema_path = Path(__file__).parent / "schema.sql"
    print(f"Applying base schema from {schema_path.name} ...")
    _run_sql_file(conn, schema_path)

    now = datetime.now(timezone.utc).isoformat()
    for mig_version, mig_file in pending:
        print(f"Applying migration {mig_file.name} (v{applied} → v{mig_version}) ...")
        _run_sql_file(conn, mig_file)
        conn.execute(
            "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
            (mig_version, now),
        )
        conn.commit()
        recorded.add(mig_version)
        applied = max(applied, mig_version)

    conn.execute(
        "INSERT OR IGNORE INTO schema_version (version, applied_at) VALUES (?, ?)",
        (_CURRENT_VERSION, now),
    )
    conn.commit()
    print(f"Migration complete. schema_version = {current_version(conn)}")


if __name__ == "__main__":
    # Allow: python3 -m moni.db.migrate
    import argparse
    from .connection import set_db_path
    parser = argparse.ArgumentParser(description="Run moni DB migrations")
    parser.add_argument("--db", default=None, help="Path to news.db (default: data/news.db)")
    args = parser.parse_args()
    if args.db:
        set_db_path(Path(args.db))
    migrate()
