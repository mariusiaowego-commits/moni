"""tests/test_llm_jobs_duration.py — ``/api/llm/jobs/stats/duration`` 的口径。

为什么需要这个端点：前端要给「这类任务通常多久」一个真数字。用户盯着一个
不知道要等 1 分钟还是 5 分钟的秒表，唯一能做的就是退出去刷新；有了历史
p50/p90，等待态才能说人话。

钉住三条口径：

1. 只统计 ``status='done' AND duration_ms > 0`` —— 失败/取消/旧空行会把
   估计带偏。
2. 样本不足 3 条时 p50/p90 回 ``None``（而不是拿 1 个样本算「通常耗时」）。
3. 路由不能被 ``/{job_id}`` 抢走（必须真返回统计对象）。
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import moni.web.routes.fact_check_plus  # noqa: F401  (import 顺序见 llm_usage_counters)
from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    set_db_path(tmp_path / "test.db")
    migrate()
    _seed_article()
    yield
    close_db()


@pytest.fixture
def client() -> TestClient:
    with TestClient(create_app()) as c:
        yield c


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


def _seed(
    job_id: str,
    *,
    task_type: str = "fact_check_plus",
    status: str = "done",
    duration_ms: int | None = 60_000,
    minutes_ago: int = 0,
) -> None:
    created = datetime.now(timezone.utc) - timedelta(minutes=minutes_ago)
    conn = get_db()
    conn.execute(
        """
        INSERT INTO llm_jobs (id, task_type, article_id, status, duration_ms, created_at)
        VALUES (?, ?, 'a1', ?, ?, ?)
        """,
        (job_id, task_type, status, duration_ms, created.isoformat()),
    )
    conn.commit()


def test_percentiles_over_recent_done_jobs(client: TestClient):
    for i, ms in enumerate((60_000, 120_000, 300_000), start=1):
        _seed(f"j{i}", duration_ms=ms, minutes_ago=i)

    body = client.get("/api/llm/jobs/stats/duration", params={"task_type": "fact_check_plus"}).json()
    assert body["n"] == 3
    assert body["p50_sec"] == 120.0
    assert body["p90_sec"] == 300.0


def test_failed_cancelled_and_zero_duration_are_excluded(client: TestClient):
    _seed("ok1", duration_ms=60_000)
    _seed("ok2", duration_ms=90_000)
    _seed("ok3", duration_ms=120_000)
    _seed("bad1", status="failed", duration_ms=10_000)
    _seed("bad2", status="cancelled", duration_ms=10_000)
    _seed("bad3", status="stale", duration_ms=10_000)
    _seed("bad4", duration_ms=0)
    _seed("bad5", duration_ms=None)

    body = client.get("/api/llm/jobs/stats/duration").json()
    assert body["n"] == 3, "失败/取消/stale/零耗时不该进样本"
    assert body["p50_sec"] == 90.0


def test_small_sample_returns_no_percentiles(client: TestClient):
    _seed("only1", duration_ms=60_000)
    _seed("only2", duration_ms=90_000)
    body = client.get("/api/llm/jobs/stats/duration").json()
    assert body == {"n": 2, "p50_sec": None, "p90_sec": None}


def test_task_type_filter_is_exact(client: TestClient):
    for i in range(3):
        _seed(f"fc{i}", task_type="fact_check_plus", duration_ms=60_000)
    for i in range(3):
        _seed(f"sm{i}", task_type="summary", duration_ms=10_000)

    body = client.get("/api/llm/jobs/stats/duration", params={"task_type": "summary"}).json()
    assert body["n"] == 3
    assert body["p50_sec"] == 10.0


def test_limit_keeps_the_newest_samples(client: TestClient):
    _seed("old", duration_ms=600_000, minutes_ago=30)
    for i, ms in enumerate((60_000, 90_000, 120_000), start=1):
        _seed(f"new{i}", duration_ms=ms, minutes_ago=i)

    body = client.get("/api/llm/jobs/stats/duration", params={"limit": 3}).json()
    assert body["n"] == 3
    assert body["p90_sec"] == 120.0, "limit 截断要按 created_at 倒序取最新，老样本不该进来"


def test_route_is_not_swallowed_by_the_job_id_wildcard(client: TestClient):
    """``/stats/duration`` 必须返回统计对象；被当成 job_id 会 404。"""
    resp = client.get("/api/llm/jobs/stats/duration")
    assert resp.status_code == 200
    assert set(resp.json()) == {"n", "p50_sec", "p90_sec"}
