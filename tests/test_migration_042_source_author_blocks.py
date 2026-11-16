"""sprint-38 — migration 042 (source_author_blocks) 测试。

覆盖 done-criteria AC-3 / AC-15 / AC-18。

口径（warden DoD v0.3.0）：
- 新表 ``source_author_blocks``，来源列存 ``source_name``（**不存** sources.id，无 FK）
- migration 幂等（重跑不报错、不重复建）
- ``_CURRENT_VERSION`` 至少包含 042；当前是 043 reader AI
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_042 = (
    REPO_ROOT / "moni" / "db" / "migrations" / "042_source_author_blocks.sql"
)
SOURCE_NAME = "xbot_feed"


@pytest.fixture
def fresh_db(tmp_path, monkeypatch):
    close_db()
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_DATA_DIR", str(data_dir))
    set_db_path(data_dir / "news.db")
    migrate()
    yield data_dir
    close_db()


def test_current_version_includes_042():
    """042 已进 schema；当前版本是 043 reader AI。"""
    assert _CURRENT_VERSION == 44


def test_migration_042_file_exists():
    """AC-18：migration 文件必须在（命名 042_source_author_blocks.sql）。"""
    assert MIGRATION_042.exists(), f"缺 migration 文件: {MIGRATION_042}"


def test_table_is_created(fresh_db):
    """AC-2：表被建出来。"""
    conn = get_db()
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' "
        "AND name='source_author_blocks'"
    ).fetchone()
    assert row is not None


def test_table_columns(fresh_db):
    """AC-2 / AC-15：列含 source_name / handle / display_name / blocked_at / notes。"""
    conn = get_db()
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(source_author_blocks)")}
    for expected in ("source_name", "handle", "display_name", "blocked_at", "notes"):
        assert expected in cols, f"缺列 {expected}"


def test_table_has_no_foreign_key_to_sources(fresh_db):
    """AC-15：来源列存 source_name → **不建** REFERENCES sources(id) 外键。"""
    conn = get_db()
    fks = list(conn.execute("PRAGMA foreign_key_list(source_author_blocks)"))
    assert fks == [], f"不该有外键: {fks}"
    ddl = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name='source_author_blocks'"
    ).fetchone()["sql"]
    assert "REFERENCES sources" not in ddl


def test_unique_constraint_on_source_name_and_handle(fresh_db):
    """AC-2：UNIQUE(source_name, handle) 保证幂等。"""
    conn = get_db()
    conn.execute(
        "INSERT INTO source_author_blocks "
        "(source_name, handle, blocked_at) VALUES (?,?,datetime('now'))",
        (SOURCE_NAME, "jiamemexyz"),
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute(
            "INSERT INTO source_author_blocks "
            "(source_name, handle, blocked_at) VALUES (?,?,datetime('now'))",
            (SOURCE_NAME, "jiamemexyz"),
        )
    conn.rollback()


def test_same_handle_allowed_for_different_sources(fresh_db):
    """AC-15：不同 source_name 可以有同名 handle（来源隔离）。"""
    conn = get_db()
    conn.execute(
        "INSERT INTO source_author_blocks "
        "(source_name, handle, blocked_at) VALUES (?,?,datetime('now'))",
        (SOURCE_NAME, "someone"),
    )
    conn.execute(
        "INSERT INTO source_author_blocks "
        "(source_name, handle, blocked_at) VALUES (?,?,datetime('now'))",
        ("36kr_rss", "someone"),
    )
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) AS c FROM source_author_blocks").fetchone()["c"]
    assert n == 2


def test_migration_042_is_idempotent(fresh_db):
    """AC-18：migration 幂等 —— 重跑不报错。"""
    conn = get_db()
    sql = MIGRATION_042.read_text(encoding="utf-8")
    # 直接重跑一遍 SQL（模拟 migrate 被重复执行）
    conn.executescript(sql)
    conn.executescript(sql)
    conn.commit()
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table' "
        "AND name='source_author_blocks'").fetchone()["c"] == 1


def test_migrate_is_idempotent(fresh_db):
    """AC-18：migrate() 重复调用安全。"""
    migrate()
    migrate()
    conn = get_db()
    assert conn.execute(
        "SELECT COUNT(*) AS c FROM sqlite_master WHERE type='table' "
        "AND name='source_author_blocks'").fetchone()["c"] == 1


def test_migration_preserves_existing_rows(fresh_db):
    """AC-3：升级路径（migrate）不能动已有名单。"""
    conn = get_db()
    conn.execute(
        "INSERT INTO source_author_blocks "
        "(source_name, handle, display_name, blocked_at) "
        "VALUES (?,?,?,datetime('now'))",
        (SOURCE_NAME, "jiamemexyz", "阿拾"),
    )
    conn.commit()
    migrate()
    rows = conn.execute(
        "SELECT handle, display_name FROM source_author_blocks").fetchall()
    assert [(r["handle"], r["display_name"]) for r in rows] == [("jiamemexyz", "阿拾")]
