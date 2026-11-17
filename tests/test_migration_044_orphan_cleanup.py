"""Migration 044: 清理三类历史孤儿行（sprint-52）。

孤儿行的根因是 2026-06 那批删除时连接没开 PRAGMA foreign_keys，CASCADE 未生效。
这个 migration 只删「article_id 不在 articles 里」的行，正常数据必须原样保留，
且重复执行不能有副作用。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import _run_sql_file, migrate

REPO_ROOT = Path(__file__).resolve().parents[1]
MIGRATION_044 = REPO_ROOT / "moni" / "db" / "migrations" / "044_orphan_rows_cleanup.sql"
NOW = "2026-11-16T00:00:00+00:00"


@pytest.fixture
def conn(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    connection = get_db()
    connection.commit()
    yield connection
    close_db()
    import moni.db.connection as conn_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None


def _seed(conn) -> None:
    conn.execute(
        "INSERT INTO articles (id, title, url, source, raw_content, fetched_at) "
        "VALUES ('keep', 't', 'https://example.com/keep', 's', 'c', ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT OR IGNORE INTO tag_definitions (name, created_at) VALUES ('tg', ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO article_tags (article_id, tag_name, predicted, created_at) "
        "VALUES ('keep', 'tg', 0, ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO article_keyword_hits (article_id, keyword, hit_field, created_at) "
        "VALUES ('keep', 'kw', 'title+content', ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO llm_jobs (id, task_type, article_id, status, created_at) "
        "VALUES ('job-keep', 'summary', 'keep', 'done', ?)",
        (NOW,),
    )
    # PRAGMA foreign_keys 在事务内是 no-op，必须先提交再关，否则插入被判 FK 失败。
    conn.commit()
    # 孤儿行：外键检查关掉才能插进来，模拟历史污染。
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute(
        "INSERT INTO article_tags (article_id, tag_name, predicted, created_at) "
        "VALUES ('ghost', 'tg', 0, ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO article_keyword_hits (article_id, keyword, hit_field, created_at) "
        "VALUES ('ghost', 'kw', 'title+content', ?)",
        (NOW,),
    )
    conn.execute(
        "INSERT INTO llm_jobs (id, task_type, article_id, status, created_at) "
        "VALUES ('job-ghost', 'summary', 'ghost', 'done', ?)",
        (NOW,),
    )
    conn.execute("PRAGMA foreign_keys = ON")
    conn.commit()


def _count(conn, table: str, where: str = "") -> int:
    sql = f"SELECT COUNT(*) FROM {table} {where}"
    return int(conn.execute(sql).fetchone()[0])


def test_orphans_removed_and_real_rows_kept(conn):
    _seed(conn)
    assert _count(conn, "article_tags", "WHERE article_id = 'ghost'") == 1

    _run_sql_file(conn, MIGRATION_044)
    conn.commit()

    assert _count(conn, "article_tags", "WHERE article_id = 'ghost'") == 0
    assert _count(conn, "article_keyword_hits", "WHERE article_id = 'ghost'") == 0
    assert _count(conn, "llm_jobs", "WHERE article_id = 'ghost'") == 0

    assert _count(conn, "article_tags", "WHERE article_id = 'keep'") == 1
    assert _count(conn, "article_keyword_hits", "WHERE article_id = 'keep'") == 1
    assert _count(conn, "llm_jobs", "WHERE article_id = 'keep'") == 1
    assert _count(conn, "articles") == 1


def test_rerun_is_idempotent(conn):
    _seed(conn)
    _run_sql_file(conn, MIGRATION_044)
    conn.commit()
    before = (
        _count(conn, "article_tags"),
        _count(conn, "article_keyword_hits"),
        _count(conn, "llm_jobs"),
    )
    _run_sql_file(conn, MIGRATION_044)
    conn.commit()
    after = (
        _count(conn, "article_tags"),
        _count(conn, "article_keyword_hits"),
        _count(conn, "llm_jobs"),
    )
    assert before == after == (1, 1, 1)


def test_fresh_database_reaches_version_44(conn):
    version = conn.execute("SELECT MAX(version) FROM schema_version").fetchone()[0]
    assert int(version) >= 44
