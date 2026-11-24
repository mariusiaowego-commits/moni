"""Tests for migration 058 (LLM desensitize rules, audit events, and default settings)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, _run_sql_file, migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_058 = REPO_ROOT / "moni" / "db" / "migrations" / "058_llm_desensitize.sql"

NOW = "2026-11-24 15:40:00"

EXPECTED_DEFAULTS = {
    "desensitize.enabled": "0",
    "desensitize.level_l1": "1",
    "desensitize.level_l2": "1",
    "desensitize.level_l3": "0",
    "desensitize.strict_fail_closed": "0",
}

EXPECTED_INDEXES = {
    "idx_desens_rules_active",
    "idx_desens_events_created",
    "idx_desens_events_task",
    "idx_desens_events_job",
    "idx_desens_events_article",
}


@pytest.fixture
def db(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    yield conn
    close_db()


def test_058_fresh_db_tables_indexes_and_schema_version(db):
    assert _CURRENT_VERSION >= 58

    tables = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {"desensitize_rules", "desensitize_events"} <= tables

    cols = {
        row[1]
        for row in db.execute("PRAGMA table_info(desensitize_events)").fetchall()
    }
    assert "items_truncated" in cols
    assert db.execute("PRAGMA foreign_key_list(desensitize_events)").fetchall() == []

    indexes = {
        row[0]
        for row in db.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
    }
    assert EXPECTED_INDEXES <= indexes

    version = db.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()[0]
    assert version == _CURRENT_VERSION


def test_058_app_settings_defaults_seeded(db):
    rows = dict(
        db.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'desensitize.%'"
        ).fetchall()
    )
    assert rows == EXPECTED_DEFAULTS


def test_058_preserves_existing_user_app_settings(tmp_path):
    close_db()
    set_db_path(tmp_path / "upgrade.db")
    migrate()
    conn = get_db()

    # Simulate an existing DB prior to 058 where user explicitly set enabled='0'.
    conn.execute("DELETE FROM app_settings WHERE key LIKE 'desensitize.%'")
    conn.execute(
        "INSERT INTO app_settings (key, value, updated_at) VALUES (?, ?, ?)",
        ("desensitize.enabled", "0", NOW),
    )
    conn.execute("DELETE FROM schema_version WHERE version >= 58")
    conn.commit()

    migrate()

    rows = dict(
        conn.execute(
            "SELECT key, value FROM app_settings WHERE key LIKE 'desensitize.%'"
        ).fetchall()
    )
    assert rows["desensitize.enabled"] == "0"
    assert rows["desensitize.level_l1"] == "1"
    assert rows["desensitize.level_l3"] == "0"
    assert rows["desensitize.strict_fail_closed"] == "0"
    close_db()


def test_058_idempotent_rerun_keeps_events_intact(db):
    db.execute(
        """
        INSERT INTO desensitize_events (
            call_id, call_site, task_type, masked_total, created_at
        ) VALUES (?, ?, ?, ?, ?)
        """,
        ("call-idempotent-1", "runner", "summary", 3, NOW),
    )
    db.commit()

    migrate()
    _run_sql_file(db, MIGRATION_058)

    count = db.execute("SELECT COUNT(*) FROM desensitize_events").fetchone()[0]
    assert count == 1


def test_058_write_and_read_event_under_foreign_keys_on(db):
    db.execute("PRAGMA foreign_keys = 1")
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1

    # Audit events deliberately omit FK to articles so records survive article deletion.
    db.execute(
        """
        INSERT INTO desensitize_events (
            call_id, job_id, article_id, call_site, task_type,
            policy_mode, masked_total, restored_total, leak_scan_hits,
            residual_placeholders, kinds_json, by_json, items_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "call-fk-1",
            "job-1",
            "deleted-article-id",
            "summary_gen",
            "summary",
            "standard",
            2,
            2,
            0,
            0,
            '{"ORG": 2}',
            '{"rule": 2}',
            '[{"kind": "ORG", "head_hint": "招", "length": 4}]',
            NOW,
        ),
    )
    db.commit()

    row = db.execute(
        "SELECT call_id, article_id, items_truncated, created_at "
        "FROM desensitize_events WHERE call_id = ?",
        ("call-fk-1",),
    ).fetchone()
    assert tuple(row) == ("call-fk-1", "deleted-article-id", 0, NOW)

    with pytest.raises(sqlite3.IntegrityError):
        db.execute(
            """
            INSERT INTO desensitize_rules (
                rule_type, pattern, created_at, updated_at
            ) VALUES ('invalid', 'foo', ?, ?)
            """,
            (NOW, NOW),
        )
    db.rollback()


def test_058_explain_query_plan_uses_created_index(db):
    db.execute(
        """
        INSERT INTO desensitize_events (
            call_id, call_site, task_type, created_at
        ) VALUES (?, ?, ?, ?)
        """,
        ("call-eqp-1", "runner", "summary", NOW),
    )
    db.commit()

    plan_rows = db.execute(
        "EXPLAIN QUERY PLAN "
        "SELECT call_id FROM desensitize_events "
        "WHERE created_at >= '2026-11-24' ORDER BY created_at"
    ).fetchall()
    plan_text = " | ".join(str(r[-1]) for r in plan_rows)
    assert "idx_desens_events_created" in plan_text
