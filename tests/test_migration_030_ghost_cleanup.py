"""Tests for moni.db migration 030 (v1.0.34.2) — drop 4 ghost sources rows.

Reproduces the /fetch page bug: 2 个 36氪 + 2 个 支付 appearing as duplicates
because the ``sources`` DB table had 4 stale test rows from 2026-08-13 with
``url=https://example.com/mp_*`` and hash-suffixed names like ``mp_3be50e90``.

Migration 030 deletes those 4 rows.  This test ensures:

1. The 4 ghost rows are deleted (idempotently — repeat run is safe).
2. Real sources (36kr_rss, mpaypass, huxiu, etc.) survive untouched.
4. Pre-existing articles / source_tags rows that reference the ghost ids
   would have been left in place (none in this case — the rows were test data
   with 0 articles / 0 tags), but the test verifies the FK-side stays clean.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, migrate


GHOST_IDS = [
    "src-mp_3be50e90",
    "src-mp_3be50e90-b",
    "src-mp_4ea4c40a",
    "src-mp_4ea4c40a-b",
]

GHOST_NAMES = [
    "mp_3be50e90",
    "mp_3be50e90_b",
    "mp_4ea4c40a",
    "mp_4ea4c40a_b",
]

REAL_SOURCES = [
    ("src-36kr-rss", "36kr_rss", "36氪 RSS"),
    ("src-mpaypass", "mpaypass", "移动支付网"),
    ("src-huxiu", "huxiu", "虎嗅"),
    ("src-qbitai", "qbitai", "量子位"),
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _seed_ghost_rows() -> None:
    """Insert the 4 ghost rows that were present before migration 030."""
    conn = get_db()
    now = _now()
    for gid, gname, dname in [
        ("src-mp_3be50e90", "mp_3be50e90", "支付"),
        ("src-mp_3be50e90-b", "mp_3be50e90_b", "36氪"),
        ("src-mp_4ea4c40a", "mp_4ea4c40a", "支付"),
        ("src-mp_4ea4c40a-b", "mp_4ea4c40a_b", "36氪"),
    ]:
        conn.execute(
            """
            INSERT INTO sources
                (id, name, display_name, source_type, url,
                 fetch_interval_min, is_active, notes, fetch_config,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (gid, gname, dname, "rss",
             f"https://example.com/{gname}", 30, 1, None, None, now, now),
        )
    conn.commit()


def _seed_real_sources() -> None:
    """Insert a handful of real sources that must survive the migration."""
    conn = get_db()
    now = _now()
    for sid, sname, dname in REAL_SOURCES:
        conn.execute(
            """
            INSERT INTO sources
                (id, name, display_name, source_type, url,
                 fetch_interval_min, is_active, notes, fetch_config,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (sid, sname, dname, "rss",
             f"https://example.com/{sname}", 30, 1, None, None, now, now),
        )
    conn.commit()


@pytest.fixture
def tmp_db(tmp_path):
    """Fresh DB, migrate, seed test data."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    yield db_path
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


class TestMigration030GhostCleanup:
    """v1.0.34.2: drop 4 stale ghost source rows from 2026-08-13."""

    def test_migration_version_bumped(self):
        """schema_version constant must match the latest migration."""
        assert _CURRENT_VERSION >= 30, (
            f"_CURRENT_VERSION must be at least 30, got {_CURRENT_VERSION}"
        )

    def test_migration_file_loads(self):
        """The migration SQL file exists and parses without syntax errors."""
        from pathlib import Path

        mig_path = (
            Path(__file__).parent.parent
            / "moni" / "db" / "migrations" / "030_drop_mp_ghost_sources.sql"
        )
        assert mig_path.exists(), f"migration file missing: {mig_path}"
        text = mig_path.read_text(encoding="utf-8")
        assert "DELETE FROM sources" in text
        for gid in GHOST_IDS:
            assert gid in text, f"migration must reference ghost id {gid}"

    def test_migration_deletes_ghost_rows(self, tmp_db):
        """After migrate(), the 4 ghost rows are gone from ``sources``."""
        # Seed ghost + real rows BEFORE migrate so we test 030's effect on
        # a pre-existing state (real prod upgrade path).
        migrate()
        _seed_ghost_rows()
        _seed_real_sources()

        # Sanity: ghost rows present before 030 runs
        conn = get_db()
        before = {
            r["id"] for r in conn.execute(
                "SELECT id FROM sources WHERE id IN ("
                + ",".join("?" * len(GHOST_IDS))
                + ")",
                GHOST_IDS,
            ).fetchall()
        }
        assert before == set(GHOST_IDS), "ghost seeds not inserted as expected"

        # Re-run only the 030 file (idempotent)
        from pathlib import Path

        from moni.db.migrate import _run_sql_file

        mig_path = (
            Path(__file__).parent.parent
            / "moni" / "db" / "migrations" / "030_drop_mp_ghost_sources.sql"
        )
        _run_sql_file(get_db(), mig_path)
        get_db().commit()

        after = {
            r["id"] for r in conn.execute(
                "SELECT id FROM sources WHERE id IN ("
                + ",".join("?" * len(GHOST_IDS))
                + ")",
                GHOST_IDS,
            ).fetchall()
        }
        assert after == set(), f"ghost rows still present: {after}"

    def test_migration_preserves_real_sources(self, tmp_db):
        """Real sources (36kr_rss, mpaypass, etc.) must NOT be deleted."""
        migrate()
        _seed_ghost_rows()
        _seed_real_sources()

        from pathlib import Path

        from moni.db.migrate import _run_sql_file

        mig_path = (
            Path(__file__).parent.parent
            / "moni" / "db" / "migrations" / "030_drop_mp_ghost_sources.sql"
        )
        _run_sql_file(get_db(), mig_path)
        get_db().commit()

        conn = get_db()
        real_ids = {r[0] for r in conn.execute(
            "SELECT id FROM sources WHERE id IN (?, ?, ?, ?)",
            [sid for sid, _, _ in REAL_SOURCES],
        ).fetchall()}
        assert real_ids == {sid for sid, _, _ in REAL_SOURCES}

        # Display names untouched
        for sid, sname, dname in REAL_SOURCES:
            row = conn.execute(
                "SELECT display_name, name FROM sources WHERE id = ?", (sid,)
            ).fetchone()
            assert row["name"] == sname
            assert row["display_name"] == dname

    def test_migration_idempotent(self, tmp_db):
        """Running 030 twice is safe — second run no-ops, no errors."""
        from pathlib import Path

        from moni.db.migrate import _run_sql_file

        migrate()
        _seed_ghost_rows()

        mig_path = (
            Path(__file__).parent.parent
            / "moni" / "db" / "migrations" / "030_drop_mp_ghost_sources.sql"
        )
        _run_sql_file(get_db(), mig_path)
        get_db().commit()
        _run_sql_file(get_db(), mig_path)
        get_db().commit()

        # schema_cleanup_log must record exactly one row (INSERT OR IGNORE)
        row = get_db().execute(
            "SELECT COUNT(*) FROM schema_cleanup_log WHERE migration = "
            "'030_drop_mp_ghost_sources'"
        ).fetchone()
        assert row[0] == 1, (
            f"schema_cleanup_log should have 1 entry, got {row[0]}"
        )

        conn = get_db()
        assert conn.execute(
            "SELECT COUNT(*) FROM sources WHERE id IN ("
            + ",".join("?" * len(GHOST_IDS))
            + ")",
            GHOST_IDS,
        ).fetchone()[0] == 0

    def test_full_migrate_on_fresh_db(self, tmp_db):
        """Fresh DB -> migrate() -> latest schema_version, no error."""
        migrate()
        conn = get_db()
        row = conn.execute(
            "SELECT COALESCE(MAX(version), 0) FROM schema_version"
        ).fetchone()
        assert row[0] == _CURRENT_VERSION >= 30

    def test_fk_cascade_cleans_orphan_tags(self, tmp_db):
        """source_tags FK → sources.id CASCADE: ghost source delete cascades.

        Schema 004 sets ``ON DELETE CASCADE`` on source_tags.source_id.
        Even if a tag were attached to a ghost row, deleting the ghost row
        cascades the tag away.  This test verifies the cascade so future
        contributors don't accidentally weaken it.

        In prod there are 0 such rows (verified via sqlite3 query), but the
        test guards against accidental FK weakening during future schema
        migrations.
        """
        migrate()
        _seed_ghost_rows()
        conn = get_db()
        # Insert tag_definitions row first (FK requirement)
        conn.execute(
            "INSERT INTO tag_definitions (name, description, color, created_at) "
            "VALUES (?, ?, ?, ?)",
            ("ghost-test-tag", "desc", "#fff", _now()),
        )
        conn.execute(
            "INSERT INTO source_tags (source_id, tag_name, created_at) "
            "VALUES (?, ?, ?)",
            ("src-mp_3be50e90", "ghost-test-tag", _now()),
        )
        conn.commit()

        from pathlib import Path

        from moni.db.migrate import _run_sql_file

        mig_path = (
            Path(__file__).parent.parent
            / "moni" / "db" / "migrations" / "030_drop_mp_ghost_sources.sql"
        )
        _run_sql_file(get_db(), mig_path)
        get_db().commit()

        # Ghost source gone
        assert conn.execute(
            "SELECT COUNT(*) FROM sources WHERE id = 'src-mp_3be50e90'"
        ).fetchone()[0] == 0
        # FK CASCADE: source_tag row gone too (no manual DELETE needed)
        assert conn.execute(
            "SELECT COUNT(*) FROM source_tags WHERE source_id = 'src-mp_3be50e90'"
        ).fetchone()[0] == 0

        # Sanity: foreign_keys must be ON for this test to be valid
        fk_on = conn.execute("PRAGMA foreign_keys").fetchone()[0]
        assert fk_on == 1, "PRAGMA foreign_keys must be ON for FK CASCADE to work"
