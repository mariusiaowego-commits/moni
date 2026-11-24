"""Tests for migration 059 (direction research guidance).

Two additive columns, both defaulting to empty text:

* ``bv_direction.research_guidance``
* ``bv_direction_research.guidance_snapshot``

An empty guidance compares equal to the empty snapshot written for rows
that existed before this column, so the live direction stays usable.
"""
from __future__ import annotations

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, migrate

NOW = "2026-11-24 00:00:00"


def test_current_version_is_at_least_59():
    # 058 is still unmerged (PR #294); the next migration will bump the
    # version again, so this must not pin an exact number.
    assert _CURRENT_VERSION >= 59


def test_fresh_database_has_empty_guidance_columns(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    direction_cols = {row[1] for row in conn.execute("PRAGMA table_info(bv_direction)")}
    research_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(bv_direction_research)")
    }
    assert "research_guidance" in direction_cols
    assert "guidance_snapshot" in research_cols

    conn.execute(
        "INSERT INTO bv_direction (label, brief, enabled, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?)",
        ("微信生态", "说明", NOW, NOW),
    )
    direction_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO bv_direction_research (direction_id, research_version, status, "
        "label_snapshot, brief_snapshot, prompt_id, started_at) "
        "VALUES (?, 1, 'passed', ?, ?, ?, ?)",
        (direction_id, "微信生态", "说明", "bv_direction_research_v1", NOW),
    )
    conn.commit()
    guidance = conn.execute("SELECT research_guidance FROM bv_direction").fetchone()[0]
    snapshot = conn.execute(
        "SELECT guidance_snapshot FROM bv_direction_research"
    ).fetchone()[0]
    assert guidance == ""
    assert snapshot == ""
    close_db()

    migrate()
    conn = get_db()
    assert conn.execute("SELECT research_guidance FROM bv_direction").fetchone()[0] == ""
    close_db()


def test_059_upgrades_a_database_that_stopped_before_it(tmp_path):
    close_db()
    set_db_path(tmp_path / "old.db")
    migrate()
    conn = get_db()
    conn.execute("ALTER TABLE bv_direction DROP COLUMN research_guidance")
    conn.execute("ALTER TABLE bv_direction_research DROP COLUMN guidance_snapshot")
    conn.execute("DELETE FROM schema_version WHERE version >= 59")
    conn.execute(
        "INSERT INTO bv_direction (label, brief, enabled, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?)",
        ("微信生态", "说明", NOW, NOW),
    )
    direction_id = conn.execute("SELECT id FROM bv_direction").fetchone()[0]
    conn.execute(
        "INSERT INTO bv_direction_research (direction_id, research_version, status, "
        "label_snapshot, brief_snapshot, prompt_id, started_at) "
        "VALUES (?, 1, 'passed', ?, ?, ?, ?)",
        (direction_id, "微信生态", "说明", "bv_direction_research_v1", NOW),
    )
    conn.commit()
    close_db()

    migrate()
    conn = get_db()
    assert conn.execute("SELECT research_guidance FROM bv_direction").fetchone()[0] == ""
    assert (
        conn.execute("SELECT guidance_snapshot FROM bv_direction_research").fetchone()[0]
        == ""
    )
    recorded = {row[0] for row in conn.execute("SELECT version FROM schema_version")}
    assert 59 in recorded
    close_db()
