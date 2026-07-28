"""Auto-migrate: apply schema.sql and incremental migrations if schema_version is behind.

v1.0 is single-schema + incremental migrations: we always run the full schema
on top of whatever exists — the CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS
guards make it idempotent.  The schema_version table tracks applied_at.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .connection import get_db, get_db_path


_CURRENT_VERSION = 21  # v1.0.10: brief_summary cache (021); 020 business_value


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
    sql = re.sub(r"--+.*", "", raw)
    for stmt in sql.split(";"):
        stmt = stmt.strip()
        if not stmt:
            continue
        try:
            conn.execute(stmt)
        except sqlite3.OperationalError as exc:
            # Ignore "duplicate column" errors from ALTER TABLE (idempotent)
            if "duplicate column" in str(exc).lower():
                pass
            else:
                raise


def migrate() -> None:
    """Run pending migrations.

    Applies schema.sql (v1 base schema) then any pending incremental migrations
    in migrations/ directory.
    """
    migrations_dir = Path(__file__).parent / "migrations"
    conn = get_db()
    applied = current_version(conn)

    if applied >= _CURRENT_VERSION:
        print(f"DB schema already at version {applied}, nothing to migrate.")
        return

    # 1. Base schema (always re-runs safely due to IF NOT EXISTS guards)
    schema_path = Path(__file__).parent / "schema.sql"
    print(f"Applying base schema from {schema_path.name} ...")
    _run_sql_file(conn, schema_path)

    # 2. Incremental migrations
    for mig_file in sorted(migrations_dir.glob("*.sql")):
        mig_name = mig_file.stem  # e.g. "003_llm_tables"
        # Extract version from prefix (e.g. "003" -> 3)
        try:
            mig_version = int(mig_name.split("_")[0])
        except (ValueError, IndexError):
            print(f"  Skipping {mig_file.name} (cannot parse version prefix)")
            continue

        if mig_version <= applied:
            continue  # already applied

        print(f"Applying migration {mig_file.name} (v{applied} → v{mig_version}) ...")
        _run_sql_file(conn, mig_file)
        applied = mig_version

    # 3. Record final version
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
        (_CURRENT_VERSION, now),
    )
    conn.commit()
    print(f"Migration complete. schema_version = {_CURRENT_VERSION}")


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
