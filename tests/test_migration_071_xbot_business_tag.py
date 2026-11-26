"""t119u — migration 071 的契约（幂等 / 只改列出的键 / 不覆盖用户数据）。"""
from __future__ import annotations

import json

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, _run_sql_file, migrate
from pathlib import Path

MIGRATION_071 = (
    Path(__file__).resolve().parents[1]
    / "moni" / "db" / "migrations" / "071_xbot_must_hit_business_tag.sql"
)
SOURCE_ID = "src-xbot-feed"


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


def _cfg() -> dict:
    row = get_db().execute(
        "SELECT fetch_config FROM sources WHERE name='xbot_feed'"
    ).fetchone()
    return json.loads(row[0])


def test_current_version_is_71():
    assert _CURRENT_VERSION == 71


def test_migration_sets_switches_and_keeps_user_keys(fresh_db):
    conn = get_db()
    # 用户先配一些东西（模拟生产：scope / 24 词黑名单 / topic_tags / 门槛）
    user_cfg = {
        "scope": {"xcircle_accounts": ["dotey"], "wechat_regex": "微信",
                  "wechat_queries": []},
        "blacklisted_handles": ["a1", "a2"],
        "topic_tags": {"wechat": ["wechat"]},
        "thresholds": {"wechat": {"min_followers": 1000, "min_likes": 0},
                       "xcircle": {"min_followers": 998, "min_likes": 0}},
        "max_days": 30,
    }
    conn.execute(
        "UPDATE sources SET fetch_config = ? WHERE name='xbot_feed'",
        (json.dumps(user_cfg, ensure_ascii=False),),
    )
    conn.commit()

    _run_sql_file(conn, MIGRATION_071)
    cfg = _cfg()

    # 发布口径（拍板第 3 条）
    assert cfg["bypass_keyword_filters"] is False
    assert cfg["bypass_tag_or_gate"] is True
    # 拍板第 1 条：scope 围栏**默认关**
    assert cfg["scope_fence_enabled"] is False
    # 拍板第 2 条：两个真相源默认都开
    assert cfg["business_tags_from_source_tags"] is True
    assert cfg["business_tags_from_topic_tags"] is True
    # 拍板第 4 条：回复/转推默认过滤
    assert cfg["exclude_replies"] is True
    assert cfg["exclude_retweets"] is True
    # 拍板第 3 条：xcircle min_likes = 20，其余键原样
    assert cfg["thresholds"]["xcircle"]["min_likes"] == 20
    assert cfg["thresholds"]["xcircle"]["min_followers"] == 998
    assert cfg["thresholds"]["wechat"] == {"min_followers": 1000, "min_likes": 0}
    assert cfg["scope"]["xcircle_accounts"] == ["dotey"]
    assert cfg["blacklisted_handles"] == ["a1", "a2"]
    assert cfg["topic_tags"] == {"wechat": ["wechat"]}
    assert cfg["max_days"] == 30


def test_migration_is_idempotent(fresh_db):
    conn = get_db()
    _run_sql_file(conn, MIGRATION_071)
    first = _cfg()
    tags_first = conn.execute(
        "SELECT name, is_active, keywords FROM tag_definitions "
        "WHERE name IN ('xbot-scope','x-ai-circle') ORDER BY name"
    ).fetchall()
    links_first = conn.execute(
        "SELECT tag_name FROM source_tags WHERE source_id=? ORDER BY 1",
        (SOURCE_ID,),
    ).fetchall()

    _run_sql_file(conn, MIGRATION_071)
    assert _cfg() == first
    assert conn.execute(
        "SELECT name, is_active, keywords FROM tag_definitions "
        "WHERE name IN ('xbot-scope','x-ai-circle') ORDER BY name"
    ).fetchall() == tags_first
    assert conn.execute(
        "SELECT tag_name FROM source_tags WHERE source_id=? ORDER BY 1",
        (SOURCE_ID,),
    ).fetchall() == links_first


def test_migration_deactivates_engineering_tag_and_seeds_loose_tag(fresh_db):
    conn = get_db()
    _run_sql_file(conn, MIGRATION_071)

    scope_tag = conn.execute(
        "SELECT is_active FROM tag_definitions WHERE name='xbot-scope'"
    ).fetchone()
    assert scope_tag is not None, "工程 tag 行不能删（历史外键要保住）"
    assert scope_tag[0] == 0, "xbot-scope 必须停用"

    loose = conn.execute(
        "SELECT display_name, color, is_active, keywords FROM tag_definitions "
        "WHERE name='x-ai-circle'"
    ).fetchone()
    assert loose is not None
    assert loose["display_name"] == "X·AI 与工程"
    assert loose["color"] == "#6366F1"
    assert loose["is_active"] == 1
    kws = json.loads(loose["keywords"])
    assert len(kws) == 37, f"37 词初始词表，实际 {len(kws)}"
    # 明确排除的泛词不得出现
    for banned in ("AI", "work", "buddy", "美元", "App"):
        assert banned not in kws, f"泛词 {banned!r} 不得进词表"
    assert "Claude Code" in kws and "MCP" in kws and "大模型" in kws


def test_migration_does_not_overwrite_existing_loose_tag_keywords(fresh_db):
    conn = get_db()
    conn.execute(
        "INSERT OR REPLACE INTO tag_definitions "
        "(name, description, color, created_at, is_active, keywords, display_name) "
        "VALUES ('x-ai-circle','mine','#111111','2026-01-01',1,?, '我的词表')",
        (json.dumps(["只留这个词"], ensure_ascii=False),),
    )
    conn.commit()
    _run_sql_file(conn, MIGRATION_071)
    row = conn.execute(
        "SELECT display_name, color, keywords FROM tag_definitions "
        "WHERE name='x-ai-circle'"
    ).fetchone()
    assert json.loads(row["keywords"]) == ["只留这个词"], "不许覆盖用户改过的词表"
    assert row["display_name"] == "我的词表"
    assert row["color"] == "#111111"


def test_migration_keeps_existing_business_tag_bindings(fresh_db):
    """不许偷偷解绑用户已勾的业务 tag（banking / APP / workbuddy 等）。"""
    conn = get_db()
    for tag in ("banking", "APP", "workbuddy"):
        conn.execute(
            "INSERT OR IGNORE INTO tag_definitions "
            "(name, description, color, created_at, is_active, keywords) "
            "VALUES (?,?,?,?,1,'[]')",
            (tag, tag, "#000000", "2026-01-01"),
        )
        conn.execute(
            "INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at) "
            "VALUES (?,?,?)",
            (SOURCE_ID, tag, "2026-01-01"),
        )
    conn.commit()
    _run_sql_file(conn, MIGRATION_071)
    links = {r[0] for r in conn.execute(
        "SELECT tag_name FROM source_tags WHERE source_id=?", (SOURCE_ID,)
    )}
    assert {"banking", "APP", "workbuddy"}.issubset(links)
    assert "x-ai-circle" in links, "松散 tag 应被建议性绑定"
