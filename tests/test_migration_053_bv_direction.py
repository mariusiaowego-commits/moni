"""Tests for migration 053 (business-value direction scoring, Sprint 67).

Three tables land here:

  * ``bv_direction``          -- the configuration (label + brief)
  * ``bv_direction_research`` -- versioned research runs, unique per direction
  * ``article_bv_direction``  -- the per-article direction score (a cache)

The interesting part is the referential shape: a direction score is a child
row of an article and of a direction, so deleting either parent must take the
score with it.  Without the cascade the table silently accumulates orphans
that no query filters out.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, _run_sql_file, migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_053 = REPO_ROOT / "moni" / "db" / "migrations" / "053_bv_direction.sql"

NOW = "2026-11-23 00:00:00"


@pytest.fixture
def db(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    yield conn
    close_db()


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


def _research(conn, direction_id: int, version: int = 1, status: str = "passed") -> int:
    cur = conn.execute(
        "INSERT INTO bv_direction_research (direction_id, research_version, status, "
        "label_snapshot, brief_snapshot, prompt_id, started_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            direction_id,
            version,
            status,
            "微信生态",
            "微信里的银行动作",
            "bv_direction_research_v1",
            NOW,
        ),
    )
    conn.commit()
    return int(cur.lastrowid)


def _score(
    conn,
    article_id: str,
    direction_id: int,
    version: int = 1,
    score: int = 72,
) -> None:
    conn.execute(
        "INSERT INTO article_bv_direction (article_id, direction_id, research_version, "
        "score, status, content_hash, scored_at) VALUES (?, ?, ?, ?, 'ready', ?, ?)",
        (article_id, direction_id, version, score, "hash", NOW),
    )
    conn.commit()


# ── 1. version ─────────────────────────────────────────────────────────


def test_053_version_constant_is_current():
    assert _CURRENT_VERSION >= 53


def test_053_fresh_db_reaches_the_current_version(db):
    version = db.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()[0]
    assert version == _CURRENT_VERSION


# ── 2. tables and indexes ─────────────────────────────────────────────


def test_053_creates_the_three_tables(db):
    names = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
    }
    assert {"bv_direction", "bv_direction_research", "article_bv_direction"} <= names


def test_053_creates_the_two_indexes(db):
    names = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type = 'index'"
        ).fetchall()
    }
    assert "idx_bvd_research_latest" in names
    assert "idx_abvd_direction_score" in names


def test_053_article_score_primary_key_is_article_direction_version(db):
    columns = {
        row[1]: (row[5], row[2])
        for row in db.execute("PRAGMA table_info(article_bv_direction)").fetchall()
    }
    assert columns["article_id"][0] == 1
    assert columns["direction_id"][0] == 2
    assert columns["research_version"][0] == 3


# ── 3. uniqueness ──────────────────────────────────────────────────────


def test_053_research_version_is_unique_per_direction(db):
    direction_id = _direction(db)
    _research(db, direction_id, 1)
    with pytest.raises(sqlite3.IntegrityError):
        _research(db, direction_id, 1)


def test_053_same_version_in_another_direction_is_allowed(db):
    first = _direction(db, "微信生态")
    second = _direction(db, "出海")
    _research(db, first, 1)
    _research(db, second, 1)
    count = db.execute(
        "SELECT COUNT(*) FROM bv_direction_research WHERE research_version = 1"
    ).fetchone()[0]
    assert count == 2


def test_053_one_article_scores_once_per_direction_and_version(db):
    article_id = _article(db)
    direction_id = _direction(db)
    _research(db, direction_id, 1)
    _score(db, article_id, direction_id, 1)
    with pytest.raises(sqlite3.IntegrityError):
        _score(db, article_id, direction_id, 1)


# ── 4. foreign keys ───────────────────────────────────────────────────


def test_053_score_for_an_unknown_article_is_rejected(db):
    direction_id = _direction(db)
    _research(db, direction_id, 1)
    with pytest.raises(sqlite3.IntegrityError):
        _score(db, "no-such-article", direction_id, 1)


def test_053_score_for_an_unknown_direction_is_rejected(db):
    article_id = _article(db)
    with pytest.raises(sqlite3.IntegrityError):
        _score(db, article_id, 999, 1)


def test_053_deleting_the_article_cascades_to_the_score(db):
    article_id = _article(db)
    direction_id = _direction(db)
    _research(db, direction_id, 1)
    _score(db, article_id, direction_id, 1)

    db.execute("DELETE FROM articles WHERE id = ?", (article_id,))
    db.commit()

    left = db.execute(
        "SELECT COUNT(*) FROM article_bv_direction WHERE article_id = ?", (article_id,)
    ).fetchone()[0]
    assert left == 0


def test_053_deleting_the_direction_cascades_to_research_and_score(db):
    article_id = _article(db)
    direction_id = _direction(db)
    _research(db, direction_id, 1)
    _score(db, article_id, direction_id, 1)

    db.execute("DELETE FROM bv_direction WHERE id = ?", (direction_id,))
    db.commit()

    assert db.execute(
        "SELECT COUNT(*) FROM bv_direction_research WHERE direction_id = ?",
        (direction_id,),
    ).fetchone()[0] == 0
    assert db.execute(
        "SELECT COUNT(*) FROM article_bv_direction WHERE direction_id = ?",
        (direction_id,),
    ).fetchone()[0] == 0


def test_053_research_for_an_unknown_direction_is_rejected(db):
    with pytest.raises(sqlite3.IntegrityError):
        _research(db, 999, 1)


# ── 5. idempotency ────────────────────────────────────────────────────


def test_053_is_idempotent(db):
    article_id = _article(db)
    direction_id = _direction(db)
    _research(db, direction_id, 1)
    _score(db, article_id, direction_id, 1)

    before = db.execute("SELECT COUNT(*) FROM article_bv_direction").fetchone()[0]
    _run_sql_file(db, MIGRATION_053)
    _run_sql_file(db, MIGRATION_053)

    after = db.execute("SELECT COUNT(*) FROM article_bv_direction").fetchone()[0]
    assert after == before
    assert db.execute("SELECT COUNT(*) FROM bv_direction").fetchone()[0] == 1
    assert db.execute("SELECT COUNT(*) FROM bv_direction_research").fetchone()[0] == 1
