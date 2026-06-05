"""Auto-migrate: apply schema.sql only if schema_version is behind.

v1.0 is single-schema: we always run the full schema on top of whatever
exists — the CREATE TABLE IF NOT EXISTS / CREATE INDEX IF NOT EXISTS guards
make it idempotent.  The schema_version table tracks applied_at.
"""

from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .connection import get_db, get_db_path


_CURRENT_VERSION = 2


def current_version(conn: sqlite3.Connection) -> int:
    """Return the currently applied schema version (0 if schema_version table doesn't exist)."""
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_version'"
    ).fetchone()
    if row is None:
        return 0
    v = conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
    return int(v)


def migrate(schema_path: Path | str | None = None) -> None:
    """Run pending migrations.

    Args:
        schema_path: Path to schema.sql.  Defaults to the sibling schema.sql
                     of this file.
    """
    if schema_path is None:
        schema_path = Path(__file__).parent / "schema.sql"
    else:
        schema_path = Path(schema_path)

    conn = get_db()
    applied = current_version(conn)

    if applied >= _CURRENT_VERSION:
        print(f"DB schema already at version {applied}, nothing to migrate.")
        return

    print(f"Applying schema v{applied + 1} → v{_CURRENT_VERSION} ...")
    raw = schema_path.read_text(encoding="utf-8")

    # Strip -- comments so they don't produce empty statements
    sql = re.sub(r"--+.*", "", raw)

    # Execute the whole clean script in one call — sqlite3 executescript
    # handles multi-statement correctly (unlike conn.executescript per-stmt
    # which can put indexes before tables if split order is wrong)
    conn.executescript(sql)

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
