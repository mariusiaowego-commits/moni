"""Claim / recovery semantics for the on-demand BV runner (v1.0.130)."""
from __future__ import annotations

import sqlite3
import time
from datetime import datetime, timedelta, timezone

import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.scoring import bv_runner
from tests.test_scoring_bv import BODY


@pytest.fixture
def conn(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    connection = get_db()
    connection.execute(
        "INSERT INTO articles (id, title, url, source, publisher, published_at, "
        "fetched_at, raw_content) VALUES (?,?,?,?,?,?,?,?)",
        ("a1", "t", "https://e.com/1", "s", "p", "2024-05-10", "2024-05-11", BODY),
    )
    connection.commit()
    yield connection
    close_db()


def _age(conn, seconds: int) -> None:
    stamp = (datetime.now(timezone.utc) - timedelta(seconds=seconds)).isoformat()
    conn.execute("UPDATE articles SET business_value_at=? WHERE id='a1'", (stamp,))
    conn.commit()


def _status(conn) -> str | None:
    return conn.execute(
        "SELECT business_value_status FROM articles WHERE id='a1'"
    ).fetchone()[0]


def test_mark_queued_claims_a_fresh_row(conn):
    assert bv_runner.mark_queued(conn, "a1") is True
    assert _status(conn) == "queued"


def test_mark_queued_refuses_a_live_row(conn):
    assert bv_runner.mark_queued(conn, "a1") is True
    assert bv_runner.mark_queued(conn, "a1") is False


def test_mark_queued_reclaims_an_abandoned_row(conn):
    bv_runner.mark_queued(conn, "a1")
    _age(conn, bv_runner.STALE_AFTER_SEC + 60)
    assert bv_runner.mark_queued(conn, "a1") is True


def test_mark_running_only_moves_queued(conn):
    bv_runner.mark_queued(conn, "a1")
    assert bv_runner.mark_running(conn, "a1") is True
    assert _status(conn) == "running"
    assert bv_runner.mark_running(conn, "a1") is False


def test_recover_stale_skips_ids_the_runner_still_owns(conn):
    bv_runner.mark_queued(conn, "a1")
    _age(conn, bv_runner.STALE_AFTER_SEC + 60)
    assert bv_runner.recover_stale(conn, live_ids={"a1"}) == 0
    assert _status(conn) == "queued"

    assert bv_runner.recover_stale(conn, live_ids=set()) == 1
    assert _status(conn) == "failed"
    error = conn.execute(
        "SELECT business_value_error FROM articles WHERE id='a1'"
    ).fetchone()[0]
    assert "中断" in error


def test_is_inflight_covers_both_states(conn):
    row = bv_runner.load_row(conn, "a1")
    assert bv_runner.is_inflight(row) is False
    bv_runner.mark_queued(conn, "a1")
    assert bv_runner.is_inflight(bv_runner.load_row(conn, "a1")) is True
    bv_runner.mark_running(conn, "a1")
    assert bv_runner.is_inflight(bv_runner.load_row(conn, "a1")) is True


def test_crash_mark_never_kills_the_queue_thread():
    """标记失败时连接已经坏了：不能把异常抛回工作线程。"""

    class _Broken:
        def execute(self, *args, **kwargs):
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")

        def commit(self):
            raise sqlite3.ProgrammingError("Cannot operate on a closed database.")

    bv_runner._mark_crashed(_Broken(), "a1")


def test_task_arriving_while_the_worker_winds_down_is_not_dropped(conn, monkeypatch):
    """队列尾部竞态：上一条刚跑完时提交的任务不能没人消费。

    旧写法在工作线程收尾时把 _pending 清空，任务就此丢失，行会一直卡在
    queued 直到 600 秒过期。这里把 close_db 拖慢，复现那个窗口。
    """
    import moni.db.connection as conn_mod

    conn.execute(
        "INSERT INTO articles (id, title, url, source, publisher, published_at, "
        "fetched_at, raw_content) VALUES (?,?,?,?,?,?,?,?)",
        ("a2", "t2", "https://e.com/2", "s", "p", "2024-05-10", "2024-05-11", BODY),
    )
    conn.commit()

    processed: list[str] = []

    def _factory(article_id: str):
        def llm(prompt: str, timeout: float):
            processed.append(article_id)
            return "", 1, "boom"

        return llm

    monkeypatch.setattr(conn_mod, "close_db", lambda: time.sleep(0.3))
    runner = bv_runner.GeneralBvRunner(_factory)

    # 认领由路由做：worker 只处理 queued → running 的行。
    assert bv_runner.mark_queued(conn, "a1") is True
    assert bv_runner.mark_queued(conn, "a2") is True
    runner.submit(["a1"])
    deadline = time.monotonic() + 5
    while "a1" not in processed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "a1" in processed

    # a1 已打完，工作线程正在收尾：这时提交 a2。
    runner.submit(["a2"])

    deadline = time.monotonic() + 5
    while "a2" not in processed and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "a2" in processed, "a2 被丢在队列里没人处理"
