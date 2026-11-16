"""Migration 041: ``bank_feed`` 源 + ``peer-banks`` tag (v1.0.76 / sprint-40)。

与 039 同一套升级契约: 只 INSERT, 绝不 UPDATE 用户已经拥有的行。
差别是 041 还要**建 tag** —— 而 tag 的 keywords 是入库闸门, 所以:

* 缺 tag 时才写 keywords (避免后续 ``moni db init`` 再 seed 时被覆盖);
* 用户已有 ``peer-banks`` 时, 用户的关键词赢;
* 只有「有效绑定」(tag 是 active) 才记 schema_cleanup_log。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _CURRENT_VERSION, _run_sql_file, migrate
from moni.db.seed_config import PEER_BANKS_KEYWORDS
from moni.fetch.sources.bank_feed import DEFAULT_FETCH_CONFIG

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_041 = (
    REPO_ROOT / "moni" / "db" / "migrations" / "041_bank_feed_source.sql"
)

SOURCE_ID = "src-bank-feed"
SOURCE_NAME = "bank_feed"
TAG = "peer-banks"


@pytest.fixture
def unseeded_db(tmp_path):
    """migrate() 之后的库 —— 041 自己已经把源和 tag 建好了。"""
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    conn = get_db()
    yield conn
    close_db()


@pytest.fixture
def stripped_db(tmp_path):
    """把 041 的产物删掉, 模拟 040 时代的库。"""
    close_db()
    db_path = tmp_path / "v40.db"
    set_db_path(db_path)
    migrate()
    conn = get_db()
    conn.execute("DELETE FROM source_tags WHERE source_id = ?", (SOURCE_ID,))
    conn.execute("DELETE FROM sources WHERE id = ?", (SOURCE_ID,))
    conn.execute("DELETE FROM tag_definitions WHERE name = ?", (TAG,))
    conn.execute("DELETE FROM schema_cleanup_log WHERE migration LIKE '041%'")
    conn.execute("DELETE FROM schema_version WHERE version >= 41")
    conn.commit()
    yield conn
    close_db()


def _row(conn) -> dict | None:
    row = conn.execute(
        "SELECT id, name, display_name, source_type, url, fetch_interval_min, "
        "is_active, notes, fetch_config, created_at FROM sources WHERE id = ?",
        (SOURCE_ID,),
    ).fetchone()
    return dict(row) if row else None


def _tag(conn) -> dict | None:
    row = conn.execute(
        "SELECT name, display_name, description, color, keywords, is_active "
        "FROM tag_definitions WHERE name = ?",
        (TAG,),
    ).fetchone()
    return dict(row) if row else None


def _links(conn) -> list[str]:
    return [
        r[0]
        for r in conn.execute(
            "SELECT tag_name FROM source_tags WHERE source_id = ? ORDER BY tag_name",
            (SOURCE_ID,),
        )
    ]


# ── 版本 ────────────────────────────────────────────────────────────────

def test_version_constant_is_current():
    assert _CURRENT_VERSION == 44


# ── 升级路径 ────────────────────────────────────────────────────────────

def test_041_creates_source_and_tag_on_v40_db(stripped_db):
    assert _row(stripped_db) is None
    assert _tag(stripped_db) is None

    _run_sql_file(stripped_db, MIGRATION_041)

    row = _row(stripped_db)
    assert row is not None
    assert row["name"] == SOURCE_NAME
    assert row["display_name"] == "银行同业"
    assert row["source_type"] == "json"
    assert row["url"] == ""
    assert row["fetch_interval_min"] == 120
    assert row["is_active"] == 1

    tag = _tag(stripped_db)
    assert tag is not None
    assert tag["display_name"] == "同业"
    assert tag["color"] == "#0F766E"
    assert tag["is_active"] == 1
    assert json.loads(tag["keywords"]) == PEER_BANKS_KEYWORDS

    assert _links(stripped_db) == [TAG]


def test_041_fetch_config_literal_matches_default(stripped_db):
    _run_sql_file(stripped_db, MIGRATION_041)
    cfg = json.loads(_row(stripped_db)["fetch_config"])
    assert cfg == DEFAULT_FETCH_CONFIG


def test_041_is_idempotent(stripped_db):
    _run_sql_file(stripped_db, MIGRATION_041)
    first = _row(stripped_db)
    _run_sql_file(stripped_db, MIGRATION_041)
    second = _row(stripped_db)
    assert second["created_at"] == first["created_at"]
    assert second["display_name"] == first["display_name"]
    assert _links(stripped_db) == [TAG]
    assert stripped_db.execute(
        "SELECT COUNT(*) FROM sources WHERE id = ?", (SOURCE_ID,)
    ).fetchone()[0] == 1


def test_041_keeps_user_edited_source(stripped_db):
    stripped_db.execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, notes, fetch_config, created_at, updated_at) "
        "VALUES (?, ?, '我改过的名字', 'json', '', 999, 0, '我的备注', NULL, "
        "'2026-01-01', '2026-01-01')",
        (SOURCE_ID, SOURCE_NAME),
    )
    stripped_db.commit()

    _run_sql_file(stripped_db, MIGRATION_041)

    row = _row(stripped_db)
    assert row["display_name"] == "我改过的名字"
    assert row["fetch_interval_min"] == 999
    assert row["is_active"] == 0
    assert row["notes"] == "我的备注"
    assert row["fetch_config"] is None
    # tag + 绑定仍然补上 (源存在了, join 就能成立)
    assert _links(stripped_db) == [TAG]


def test_041_keeps_user_owned_tag_keywords(stripped_db):
    stripped_db.execute(
        "INSERT INTO tag_definitions (name, display_name, description, color, "
        "keywords, created_at, is_active) "
        "VALUES (?, '我的同业', '我的描述', '#000000', ?, '2026-01-01', 1)",
        (TAG, json.dumps(["我自己的词"], ensure_ascii=False)),
    )
    stripped_db.commit()

    _run_sql_file(stripped_db, MIGRATION_041)

    tag = _tag(stripped_db)
    assert tag["display_name"] == "我的同业"
    assert tag["color"] == "#000000"
    assert json.loads(tag["keywords"]) == ["我自己的词"]


def test_041_guard_skips_log_when_tag_inactive(stripped_db):
    stripped_db.execute(
        "INSERT INTO tag_definitions (name, display_name, description, color, "
        "keywords, created_at, is_active) "
        "VALUES (?, '同业', '用户停用的', '#0F766E', ?, '2026-01-01', 0)",
        (TAG, json.dumps(PEER_BANKS_KEYWORDS, ensure_ascii=False)),
    )
    stripped_db.commit()

    _run_sql_file(stripped_db, MIGRATION_041)

    # 绑定写进去了, 但 tag 是 inactive → 闸门实际是 pass-through → 不许记成功
    assert _links(stripped_db) == [TAG]
    assert stripped_db.execute(
        "SELECT COUNT(*) FROM schema_cleanup_log WHERE migration = '041_bank_feed_source'"
    ).fetchone()[0] == 0


def test_041_logs_cleanup_on_success(stripped_db):
    _run_sql_file(stripped_db, MIGRATION_041)
    assert stripped_db.execute(
        "SELECT COUNT(*) FROM schema_cleanup_log WHERE migration = '041_bank_feed_source'"
    ).fetchone()[0] == 1


def test_041_no_dangling_links(stripped_db):
    stripped_db.execute("DELETE FROM tag_definitions WHERE name = ?", (TAG,))
    stripped_db.commit()
    _run_sql_file(stripped_db, MIGRATION_041)
    # 缺 tag 时 041 自己会建; 确认 link 的 tag 一定存在
    for tag_name in _links(stripped_db):
        assert stripped_db.execute(
            "SELECT 1 FROM tag_definitions WHERE name = ?", (tag_name,)
        ).fetchone() is not None


def test_041_sql_literals_have_no_semicolon_or_dashes():
    """migrate.py 的兜底路径按 ';' 切分并剔除 '--' 之后的内容。"""
    sql = MIGRATION_041.read_text(encoding="utf-8")
    literals = re.findall(r"'([^']*)'", sql)
    assert literals
    for literal in literals:
        assert ";" not in literal
        assert "--" not in literal
        assert "/*" not in literal


def test_041_fresh_db_via_migrate(unseeded_db):
    version = unseeded_db.execute(
        "SELECT COALESCE(MAX(version), 0) FROM schema_version"
    ).fetchone()[0]
    assert version == 44
    assert _row(unseeded_db) is not None
    assert _tag(unseeded_db) is not None
    assert _links(unseeded_db) == [TAG]
