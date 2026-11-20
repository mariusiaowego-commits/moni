"""tests/test_llm_jobs_heartbeat.py — 运行中续租 + 终态前的顺序 (v1.0.110).

`leases.acquire` 只在认领时写一次 `lease_heartbeat_at`。联网核查要跑
2-5 分钟，超过 90s grace 后，**另一个进程**（8901 / CLI）的 worker 会把它
sweep 成 `stale`，随后收尾的 `leases.finish()` 被 fence 掉（rowcount=0）——
文章写进去了、任务却显示异常。

这个文件锁三件事：

1. 运行期真的续租，而且任务结束后线程不再跳。
2. 心跳停止（join）严格早于任何 `leases.finish()`。
3. 早期失败分支（article 不存在等）也要把 lease 释放掉。
"""

from __future__ import annotations

import os
import time

import pytest

from moni.db.connection import close_db, get_db, set_db_path


@pytest.fixture(autouse=True)
def _isolated_db(tmp_path, monkeypatch):
    test_db = str(tmp_path / "moni-test-heartbeat.db")
    monkeypatch.setenv("MONI_DB_PATH", test_db)
    set_db_path(None)
    close_db()
    try:
        os.unlink(test_db)
    except FileNotFoundError:
        pass
    from moni.db.migrate import migrate

    migrate()
    yield
    close_db()


def _seed_article(article_id: str = "a1") -> None:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO articles (id, title, url, source, fetched_at, raw_content)
        VALUES (?, 't', 'https://example.com/a', 'test', '2026-11-20T00:00:00Z', '正文内容足够长')
        """,
        (article_id,),
    )
    conn.commit()


def _insert_job(job_id: str, article_id: str, task_type: str = "fact_check",
                created_at: str = "2026-11-20T00:00:00Z") -> None:
    conn = get_db()
    conn.execute(
        """
        INSERT INTO llm_jobs (id, task_type, article_id, provider, model, status, attempts, created_at)
        VALUES (?, ?, ?, 'profile', '', 'pending', 0, ?)
        """,
        (job_id, task_type, article_id, created_at),
    )
    conn.commit()


def test_beat_renews_while_running_and_stops_after(monkeypatch):
    from moni.llm import jobs

    monkeypatch.setattr(jobs, "HEARTBEAT_INTERVAL_SEC", 0.01)
    calls: list[float] = []
    monkeypatch.setattr(
        jobs.leases, "heartbeat", lambda handle: calls.append(time.monotonic()) or True
    )

    handle = jobs.leases.LeaseHandle(job_id="j1", owner="o", fence=1, table="llm_jobs")
    result = jobs._run_with_heartbeat(lambda: time.sleep(0.06) or "ok", handle)

    assert result == "ok"
    assert len(calls) >= 2, f"运行期没有续租: {len(calls)} 次"
    settled = len(calls)
    time.sleep(0.05)
    assert len(calls) == settled, "任务结束后心跳线程还在跳"


def test_beat_stops_before_terminal_write(monkeypatch):
    """finish 之后不允许再有 heartbeat —— 否则终态写与续租互相打脸。"""
    from moni.llm import jobs

    monkeypatch.setattr(jobs, "HEARTBEAT_INTERVAL_SEC", 0.01)
    events: list[str] = []
    monkeypatch.setattr(jobs.leases, "heartbeat", lambda h: events.append("hb") or True)

    real_finish = jobs.leases.finish

    def fake_finish(handle, **kwargs):
        events.append("finish")
        return True

    monkeypatch.setattr(jobs.leases, "finish", fake_finish)
    monkeypatch.setattr(jobs, "call_llm", lambda *a, **k: (time.sleep(0.05) or ("结论", {})))

    _seed_article("a1")
    _insert_job("j-order", "a1")
    handle = jobs.leases.acquire("llm_jobs", grace_sec=90)
    assert handle is not None
    row = dict(get_db().execute("SELECT * FROM llm_jobs WHERE id='j-order'").fetchone())

    jobs._run_job(row, handle)
    jobs.leases.finish = real_finish

    assert "finish" in events
    first_finish = events.index("finish")
    assert "hb" not in events[first_finish:], f"finish 之后还在续租: {events}"


def test_missing_article_releases_lease(monkeypatch):
    from moni.llm import jobs

    # 允许插入指向不存在文章的 job：_run_job 的「文章已删除」分支靠这条路径触发。
    conn = get_db()
    conn.execute("PRAGMA foreign_keys=OFF")
    _insert_job("j-missing", "no-such-article")
    conn.execute("PRAGMA foreign_keys=ON")
    handle = jobs.leases.acquire("llm_jobs", grace_sec=90)
    assert handle is not None
    job_id = handle.job_id
    row = dict(get_db().execute("SELECT * FROM llm_jobs WHERE id=?", (job_id,)).fetchone())

    jobs._run_job(row, handle)

    got = get_db().execute(
        "SELECT status, lease_owner, lease_heartbeat_at FROM llm_jobs WHERE id=?", (job_id,)
    ).fetchone()
    assert got["status"] == "failed"
    assert got["lease_owner"] is None, "早期失败分支没有释放 lease"


def test_list_jobs_ties_break_by_id(monkeypatch):
    from moni.llm import jobs

    _seed_article("a1")
    same = "2026-11-20T01:02:03.456Z"
    _insert_job("aaa111", "a1", created_at=same)
    _insert_job("zzz999", "a1", created_at=same)

    rows = jobs.list_jobs(article_id="a1", limit=10)
    assert [r["id"] for r in rows] == ["zzz999", "aaa111"], "同一 created_at 时顺序不稳定"
