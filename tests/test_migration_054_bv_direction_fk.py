"""Tests for migration 054 (rebuild article_bv_direction with cascade FKs).

Migration 053 created ``article_bv_direction`` without foreign keys, and
``CREATE TABLE IF NOT EXISTS`` cannot add a constraint to a table that already
exists.  A database that ran the first version of 053 therefore kept the
constraint-less shape -- 053 re-applied does nothing, and it is recorded as
applied, so nothing ever fixes it.

054 rebuilds the table with the constraints and copies the rows across.  The
copy runs with ``foreign_keys`` off, because the old table carries no
constraints and may already hold rows whose article or direction is gone.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, _run_sql_file, migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_054 = REPO_ROOT / "moni" / "db" / "migrations" / "054_bv_direction_fk.sql"

NOW = "2026-11-23 00:00:00"

#: The table shape 053 used before the foreign keys were added.
OLD_TABLE = """
CREATE TABLE article_bv_direction (
    article_id          TEXT NOT NULL,
    direction_id        INTEGER NOT NULL,
    research_version    INTEGER NOT NULL,
    score               INTEGER,
    note                TEXT,
    status              TEXT NOT NULL,
    error               TEXT,
    breakdown_json      TEXT,
    content_hash        TEXT NOT NULL,
    rubric_id           TEXT,
    scored_at           TEXT NOT NULL,
    PRIMARY KEY (article_id, direction_id, research_version)
);
"""


@pytest.fixture
def db(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    yield conn
    close_db()


def _fk_targets(conn, table: str) -> set[str]:
    return {row[2] for row in conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()}


def _article(conn, article_id: str = "a1") -> str:
    conn.execute(
        "INSERT OR IGNORE INTO articles (id, title, url, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (article_id, "标题", f"https://example.com/{article_id}", "wechat_rss_a", NOW),
    )
    conn.commit()
    return article_id


def _direction(conn, label: str = "微信生态") -> int:
    cur = conn.execute(
        "INSERT INTO bv_direction (label, brief, enabled, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?)",
        (label, "微信里的银行动作", NOW, NOW),
    )
    conn.commit()
    return int(cur.lastrowid)


def _score(conn, article_id: str, direction_id: int, score: int = 72) -> None:
    conn.execute(
        "INSERT INTO article_bv_direction (article_id, direction_id, research_version, "
        "score, status, content_hash, scored_at) VALUES (?, ?, 1, ?, 'ready', 'h', ?)",
        (article_id, direction_id, score, NOW),
    )
    conn.commit()


def _downgrade_to_the_old_shape(conn) -> None:
    """Turn the table back into the constraint-less shape 053 used."""
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DROP TABLE article_bv_direction")
    conn.execute(OLD_TABLE)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


# ── 1. a fresh database ───────────────────────────────────────────────


def test_054_version_constant_is_current():
    assert _CURRENT_VERSION >= 54


def test_054_fresh_db_has_both_foreign_keys(db):
    assert _fk_targets(db, "article_bv_direction") == {"articles", "bv_direction"}


def test_054_fresh_db_reaches_the_current_version(db):
    version = db.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()[0]
    assert version == _CURRENT_VERSION


def test_054_leaves_foreign_keys_enabled_afterwards(db):
    _run_sql_file(db, MIGRATION_054)
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


# ── 2. the upgrade path from the constraint-less shape ────────────────


def test_054_adds_the_foreign_keys_to_an_old_database(db):
    _downgrade_to_the_old_shape(db)
    assert _fk_targets(db, "article_bv_direction") == set()

    _run_sql_file(db, MIGRATION_054)

    assert _fk_targets(db, "article_bv_direction") == {"articles", "bv_direction"}


def test_054_keeps_the_rows_across_the_rebuild(db):
    article_id = _article(db)
    direction_id = _direction(db)
    _downgrade_to_the_old_shape(db)
    _score(db, article_id, direction_id, 88)

    _run_sql_file(db, MIGRATION_054)

    rows = db.execute(
        "SELECT article_id, direction_id, research_version, score, status, "
        "content_hash FROM article_bv_direction"
    ).fetchall()
    assert len(rows) == 1
    assert tuple(rows[0]) == (article_id, direction_id, 1, 88, "ready", "h")


def test_054_keeps_the_index_after_the_rebuild(db):
    _downgrade_to_the_old_shape(db)
    _run_sql_file(db, MIGRATION_054)
    names = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }
    assert "idx_abvd_direction_score" in names


def test_054_survives_an_orphan_row(db):
    """The old table had no constraints, so it may hold a dangling score.

    Refusing the copy would abort the whole upgrade, so the row is carried
    over as it is instead of failing the migration.
    """
    direction_id = _direction(db)
    _downgrade_to_the_old_shape(db)
    _score(db, "article-that-does-not-exist", direction_id, 55)

    _run_sql_file(db, MIGRATION_054)

    count = db.execute("SELECT COUNT(*) FROM article_bv_direction").fetchone()[0]
    assert count == 1


def test_054_rejects_a_new_dangling_row_afterwards(db):
    """Once the constraints exist, a new dangling row is refused."""
    direction_id = _direction(db)
    with pytest.raises(sqlite3.IntegrityError):
        _score(db, "article-that-does-not-exist", direction_id, 55)


# ── 3. the cascades still work after the rebuild ──────────────────────


def test_054_cascade_on_article_delete_survives_the_rebuild(db):
    article_id = _article(db)
    direction_id = _direction(db)
    _downgrade_to_the_old_shape(db)
    _score(db, article_id, direction_id)
    _run_sql_file(db, MIGRATION_054)

    db.execute("DELETE FROM articles WHERE id = ?", (article_id,))
    db.commit()

    assert db.execute(
        "SELECT COUNT(*) FROM article_bv_direction WHERE article_id = ?", (article_id,)
    ).fetchone()[0] == 0


def test_054_cascade_on_direction_delete_survives_the_rebuild(db):
    article_id = _article(db)
    direction_id = _direction(db)
    _downgrade_to_the_old_shape(db)
    _score(db, article_id, direction_id)
    _run_sql_file(db, MIGRATION_054)

    db.execute("DELETE FROM bv_direction WHERE id = ?", (direction_id,))
    db.commit()

    assert db.execute(
        "SELECT COUNT(*) FROM article_bv_direction WHERE direction_id = ?",
        (direction_id,),
    ).fetchone()[0] == 0


# ── 4. idempotency ────────────────────────────────────────────────────


def test_054_is_idempotent(db):
    article_id = _article(db)
    direction_id = _direction(db)
    _score(db, article_id, direction_id, 88)

    _run_sql_file(db, MIGRATION_054)
    _run_sql_file(db, MIGRATION_054)

    rows = db.execute("SELECT * FROM article_bv_direction").fetchall()
    assert len(rows) == 1
    assert _fk_targets(db, "article_bv_direction") == {"articles", "bv_direction"}
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_054_leaves_no_staging_table_behind(db):
    _run_sql_file(db, MIGRATION_054)
    names = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert "article_bv_direction_new" not in names


# ── 5. the runner's fallback path ─────────────────────────────────────


def test_054_survives_the_runner_fallback(db):
    """migrate.py's fallback runs the statements one by one after a split on ';'.

    The script must not rely on executescript-only behaviour -- a transaction
    block would break the per-statement path, so 054 uses none.
    """
    import sqlite3 as _sqlite3

    class _BrokenExecutescript:
        """Proxy that refuses executescript, forcing the fallback."""

        def __init__(self, conn):
            self._conn = conn

        def executescript(self, sql):  # noqa: ANN001
            raise _sqlite3.OperationalError("forced fallback for the test")

        def execute(self, sql, *args):  # noqa: ANN001
            return self._conn.execute(sql, *args)

        def __getattr__(self, name):
            return getattr(self._conn, name)

    article_id = _article(db)
    direction_id = _direction(db)
    _downgrade_to_the_old_shape(db)
    _score(db, article_id, direction_id, 88)

    _run_sql_file(_BrokenExecutescript(db), MIGRATION_054)

    assert _fk_targets(db, "article_bv_direction") == {"articles", "bv_direction"}
    assert db.execute("SELECT COUNT(*) FROM article_bv_direction").fetchone()[0] == 1
