"""tests/test_llm_jobs_queue.py — pending 任务的排队位次。

前端要能说「第 2/3 位」而不是干巴巴一个「排队中」。位次必须是**服务端
算的真值**（FIFO 顺序 + 正在跑的那条也算在前面），不能是前端自己数的
「我点了几个」。

口径：
* ``queue_position`` = 正在跑的数量 + 1 + 我前面的 pending 数（1 基）。
* ``queue_total``    = 正在跑 + 全部 pending。
* 非 pending 的任务两个字段都为 ``None``（前端据此不显示位次）。
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


def _seed(job_id: str, status: str, *, seconds_ago: int) -> None:
    created = datetime.now(timezone.utc) - timedelta(seconds=seconds_ago)
    conn = get_db()
    conn.execute(
        """
        INSERT INTO llm_jobs (id, task_type, article_id, status, created_at)
        VALUES (?, 'fact_check_plus', 'a1', ?, ?)
        """,
        (job_id, status, created.isoformat()),
    )
    conn.commit()


def test_pending_jobs_are_ranked_fifo_behind_the_running_one(client: TestClient):
    _seed("running1", "running", seconds_ago=120)
    _seed("p_old", "pending", seconds_ago=60)
    _seed("p_new", "pending", seconds_ago=10)

    rows = {j["id"]: j for j in client.get("/api/llm/jobs").json()}
    assert rows["running1"]["queue_position"] is None
    assert rows["p_old"]["queue_position"] == 2
    assert rows["p_new"]["queue_position"] == 3
    assert rows["p_old"]["queue_total"] == rows["p_new"]["queue_total"] == 3


def test_single_job_endpoint_exposes_the_same_position(client: TestClient):
    _seed("only", "pending", seconds_ago=5)
    body = client.get("/api/llm/jobs/only").json()
    assert body["queue_position"] == 1
    assert body["queue_total"] == 1


def test_finished_jobs_carry_no_queue_fields(client: TestClient):
    _seed("done1", "done", seconds_ago=30)
    body = client.get("/api/llm/jobs/done1").json()
    assert body["queue_position"] is None
    assert body["queue_total"] is None


def test_status_filter_accepts_stale(client: TestClient):
    """leases 判死的行是真实存在的状态，过滤白名单漏了它前端就查不到。"""
    _seed("zombie", "stale", seconds_ago=30)
    rows = client.get("/api/llm/jobs", params={"status": "stale"}).json()
    assert [r["id"] for r in rows] == ["zombie"]
    assert rows[0]["phase"] == "stale"


def test_reader_chat_is_not_in_the_reading_queue(client: TestClient):
    """reader_chat 走独立 worker，不占阅读器摘要/核查的排队位次。"""
    _seed("chat1", "running", seconds_ago=120)
    get_db().execute("UPDATE llm_jobs SET task_type = 'reader_chat' WHERE id = 'chat1'")
    get_db().commit()
    _seed("p1", "pending", seconds_ago=30)

    rows = {j["id"]: j for j in client.get("/api/llm/jobs").json()}
    assert rows["p1"]["queue_position"] == 1
    assert rows["p1"]["queue_total"] == 1
