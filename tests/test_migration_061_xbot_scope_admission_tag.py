"""migration 061 — X-bot scope 准入的专用 tag (sprint-55 / v1.0.131)。

为什么要有这个 tag: 被 scope 单独救回的条目必须挂 tag（AGENTS.md 铁律 3），
但**不能**冒充领域关键词命中。所以它是一个 keywords 为空的**纯归属** tag。

这里钉死四件事:
1. 迁移后 ``xbot-scope`` 存在、active、``keywords == []``、有 display_name;
2. 它**不绑定任何源**（不进 ``source_tags``），因此不会变成 tag 闸门候选;
3. ``_load_tag_keywords(['xbot-scope'])`` 返回空 → 对候选 tag 的关键词闸门零影响;
4. 迁移幂等（重复跑不报错、不产生第二行）。
"""
from __future__ import annotations

import json

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, migrate

_TAG = "xbot-scope"


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


def test_current_version_at_least_61():
    assert _CURRENT_VERSION >= 61, (
        f"migration 061 需要 _CURRENT_VERSION >= 61, 实际 {_CURRENT_VERSION}"
    )


def test_scope_tag_exists_and_is_active(fresh_db):
    row = get_db().execute(
        "SELECT name, description, color, is_active, keywords, display_name "
        "FROM tag_definitions WHERE name = ?", (_TAG,)).fetchone()
    assert row is not None, "migration 061 没有建出 xbot-scope tag"
    assert row["is_active"] == 1
    assert row["display_name"] == "X-bot 范围"
    assert row["color"] == "#0EA5E9"
    assert "准入" in row["description"]


def test_scope_tag_keywords_are_empty(fresh_db):
    """keywords 必须为空数组 —— 一旦有词, 它会变成 tag 闸门候选。"""
    raw = get_db().execute(
        "SELECT keywords FROM tag_definitions WHERE name = ?", (_TAG,)).fetchone()["keywords"]
    assert raw is not None
    assert json.loads(raw) == []


def test_scope_tag_is_not_bound_to_any_source(fresh_db):
    n = get_db().execute(
        "SELECT COUNT(*) FROM source_tags WHERE tag_name = ?", (_TAG,)).fetchone()[0]
    assert n == 0, "xbot-scope 是纯归属 tag，不该绑定任何源"


def test_scope_tag_contributes_no_gate_keywords(fresh_db):
    """它对 ``_load_tag_keywords`` 零贡献 —— 不能改变任何源的准入门槛。"""
    from moni.fetch.sources.xbot_feed import _load_tag_keywords

    assert _load_tag_keywords([_TAG]) == {}


def test_migration_061_is_idempotent(fresh_db):
    from pathlib import Path

    from moni.db.migrate import _run_sql_file

    sql = (Path(__file__).resolve().parents[1] / "moni" / "db" / "migrations"
           / "061_xbot_scope_admission_tag.sql")
    conn = get_db()
    _run_sql_file(conn, sql)
    conn.commit()
    _run_sql_file(conn, sql)
    conn.commit()
    n = conn.execute(
        "SELECT COUNT(*) FROM tag_definitions WHERE name = ?", (_TAG,)).fetchone()[0]
    assert n == 1
