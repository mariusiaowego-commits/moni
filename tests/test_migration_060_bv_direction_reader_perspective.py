"""Tests for migration 060 (reader perspective and clarification round).

Three additive columns plus one new table.  Existing rows get '' on all
three columns, which compares equal to the empty snapshots, so the live
direction keeps its passed research and stays usable.
"""
from __future__ import annotations

import json

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, migrate
from moni.scoring.direction import usable_research

NOW = "2026-11-25 00:00:00"


def test_current_version_is_at_least_60():
    assert _CURRENT_VERSION >= 60


def test_fresh_database_has_the_columns_and_the_table(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    direction_cols = {row[1] for row in conn.execute("PRAGMA table_info(bv_direction)")}
    research_cols = {
        row[1] for row in conn.execute("PRAGMA table_info(bv_direction_research)")
    }
    assert "reader_perspective" in direction_cols
    assert "perspective_snapshot" in research_cols
    assert "clarification_snapshot" in research_cols

    tables = {
        row[0]
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    }
    assert "bv_direction_clarification" in tables
    close_db()


def test_existing_direction_keeps_its_passed_research(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    conn.execute(
        "INSERT INTO bv_direction (label, brief, enabled, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?)",
        ("微信生态", "说明", NOW, NOW),
    )
    direction_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO bv_direction_research (direction_id, research_version, status, "
        "label_snapshot, brief_snapshot, prompt_id, scope, out_of_scope, "
        "rubric_json, started_at) "
        "VALUES (?, 1, 'passed', ?, ?, ?, ?, ?, ?, ?)",
        (
            direction_id,
            "微信生态",
            "说明",
            "bv_direction_research_v1",
            "scope",
            "out",
            '{"dimensions": {}}',
            NOW,
        ),
    )
    conn.commit()

    perspective = conn.execute(
        "SELECT reader_perspective FROM bv_direction"
    ).fetchone()[0]
    snapshot = conn.execute(
        "SELECT perspective_snapshot FROM bv_direction_research"
    ).fetchone()[0]
    clarification = conn.execute(
        "SELECT clarification_snapshot FROM bv_direction_research"
    ).fetchone()[0]
    assert perspective == ""
    assert snapshot == ""
    assert clarification == ""
    close_db()


def test_clarification_table_follows_the_direction(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    conn.execute(
        "INSERT INTO bv_direction (label, brief, enabled, created_at, updated_at) "
        "VALUES (?, ?, 1, ?, ?)",
        ("微信生态", "说明", NOW, NOW),
    )
    direction_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    conn.execute(
        "INSERT INTO bv_direction_clarification (direction_id, clarification_version, "
        "status, perspective_snapshot, questions_json, answers_json, prompt_id, "
        "started_at) VALUES (?, 1, 'passed', ?, '[]', '[]', ?, ?)",
        (direction_id, "视角", "bv_direction_clarify_v1", NOW),
    )
    conn.commit()
    assert (
        conn.execute("SELECT COUNT(*) FROM bv_direction_clarification").fetchone()[0]
        == 1
    )
    conn.execute("DELETE FROM bv_direction WHERE id=?", (direction_id,))
    conn.commit()
    assert (
        conn.execute("SELECT COUNT(*) FROM bv_direction_clarification").fetchone()[0]
        == 0
    )
    close_db()


def test_empty_snapshots_still_gate_open(tmp_path):
    """The live direction's v1 research must survive the new columns."""
    from tests.test_scoring_direction import _payload

    rows = [
        {
            "direction_id": 1,
            "research_version": 3,
            "status": "passed",
            "label_snapshot": "微信生态",
            "brief_snapshot": "说明",
            "guidance_snapshot": "",
            "perspective_snapshot": "",
            "clarification_snapshot": "",
            "prompt_id": "bv_direction_research_v1",
            "scope": "scope",
            "out_of_scope": "out",
            "rubric_json": json.dumps(_payload(), ensure_ascii=False),
        }
    ]
    research, reason = usable_research(
        rows, label="微信生态", brief="说明", reader_perspective="", direction_id=1
    )
    assert research is not None, reason
    assert research.version == 3
    assert research.reader_perspective == ""


def test_a_new_perspective_does_not_match_the_old_snapshot(tmp_path):
    """Setting a perspective invalidates the v1 research until it is redone."""
    from tests.test_scoring_direction import _payload

    rows = [
        {
            "direction_id": 1,
            "research_version": 3,
            "status": "passed",
            "label_snapshot": "微信生态",
            "brief_snapshot": "说明",
            "guidance_snapshot": "",
            "perspective_snapshot": "",
            "clarification_snapshot": "",
            "prompt_id": "bv_direction_research_v1",
            "scope": "scope",
            "out_of_scope": "out",
            "rubric_json": json.dumps(_payload(), ensure_ascii=False),
        }
    ]
    research, reason = usable_research(
        rows,
        label="微信生态",
        brief="说明",
        reader_perspective="银行新媒体渠道从业者",
        direction_id=1,
    )
    assert research is None
    assert reason and "提示词版本不匹配" in reason
