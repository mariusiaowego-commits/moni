"""API tests for on-demand general business-value scoring (v1.0.130)."""
from __future__ import annotations

import threading
import time
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.scoring import rubric
from moni.web.routes import bv_scores as bv_route
from tests.test_scoring_bv import BODY, make_llm


def _scores(value: int) -> dict:
    return {
        key: {"score": value, "evidence_ids": ["e1", "e2", "e3"], "reason": f"{key} 有事实"}
        for key in rubric.KEYS
    }


@pytest.fixture
def client(tmp_path, monkeypatch):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    from moni.web.server import create_app

    calls = {"n": 0}

    def _counting(article_id: str):
        base = make_llm(scores=_scores(7))

        def llm(prompt: str, timeout: float):
            calls["n"] += 1
            return base(prompt, timeout)

        return llm

    monkeypatch.setattr(bv_route, "_llm_call", _counting)
    with TestClient(create_app()) as test_client:
        bv_route._runner = None
        test_client.llm_calls = calls
        yield test_client
        if bv_route._runner is not None:
            bv_route._runner.wait_idle(timeout=30)
        bv_route._runner = None
    close_db()


def _add_article(article_id: str = "a1", body: str = BODY) -> str:
    conn = get_db()
    conn.execute(
        "INSERT INTO articles (id, title, url, source, publisher, published_at, "
        "fetched_at, raw_content) VALUES (?,?,?,?,?,?,?,?)",
        (article_id, "标题", "https://e.com/1", "s", "p", "2024-05-10", "2024-05-11", body),
    )
    conn.commit()
    return article_id


def _wait(client: TestClient, article_id: str, timeout: float = 20.0) -> dict:
    deadline = time.monotonic() + timeout
    state: dict = {}
    while time.monotonic() < deadline:
        state = client.get(f"/api/articles/{article_id}/business-value").json()
        if state.get("status") not in ("queued", "running"):
            return state
        time.sleep(0.05)
    return state


def test_unknown_article_is_404(client):
    assert client.post("/api/articles/nope/business-value").status_code == 404
    assert client.get("/api/articles/nope/business-value").status_code == 404


def test_post_scores_the_article_and_get_returns_it(client):
    article_id = _add_article()
    response = client.post(f"/api/articles/{article_id}/business-value")
    assert response.status_code == 202
    assert response.json()["queued"] is True

    state = _wait(client, article_id)
    assert state["status"] == "ready"
    assert state["score"] == 70
    assert state["stale"] is False
    assert [d["key"] for d in state["breakdown"]["dimensions"]] == list(rubric.KEYS)
    # 总分由代码按权重算，十维 7 分 → 70，模型从不给总分
    assert state["breakdown"]["total"] == 70


def test_second_post_is_cached_when_body_is_unchanged(client):
    article_id = _add_article()
    client.post(f"/api/articles/{article_id}/business-value")
    _wait(client, article_id)

    again = client.post(f"/api/articles/{article_id}/business-value")
    body = again.json()
    assert again.status_code == 202
    assert body["cached"] is True
    assert body["queued"] is False


def test_force_rescores_a_settled_article(client):
    article_id = _add_article()
    client.post(f"/api/articles/{article_id}/business-value")
    _wait(client, article_id)

    forced = client.post(f"/api/articles/{article_id}/business-value?force=true")
    assert forced.json()["queued"] is True
    state = _wait(client, article_id)
    assert state["status"] == "ready"


def test_short_body_ends_as_insufficient_without_model_calls(client):
    article_id = _add_article("a2", body="短" * 199)
    # insufficient 不是可缓存的完成态，POST 会排队；但管线不调模型直接返回。
    client.post(f"/api/articles/{article_id}/business-value")
    state = _wait(client, article_id)
    assert state["status"] == "insufficient"
    assert state["score"] is None


def test_stale_queued_row_can_be_claimed_again(client):
    article_id = _add_article("a3")
    conn = get_db()
    conn.execute(
        "UPDATE articles SET business_value_status='queued', "
        "business_value_at='2020-01-01T00:00:00+00:00' WHERE id=?",
        (article_id,),
    )
    conn.commit()

    response = client.post(f"/api/articles/{article_id}/business-value")
    assert response.json()["queued"] is True
    assert _wait(client, article_id)["status"] == "ready"


def test_live_row_is_reported_as_running_not_queued_twice(client):
    article_id = _add_article("a4")
    conn = get_db()

    # 模拟本进程正在跑：状态 queued + 时间戳是现在。
    conn.execute(
        "UPDATE articles SET business_value_status='queued', business_value_at=? WHERE id=?",
        (datetime.now(timezone.utc).isoformat(), article_id),
    )
    conn.commit()
    response = client.post(f"/api/articles/{article_id}/business-value")
    assert response.status_code == 202
    body = response.json()
    assert body["running"] is True
    assert body["queued"] is False
    # 别人的活儿还在跑：一次模型调用都不该发生。
    assert client.llm_calls["n"] == 0
    state = client.get(f"/api/articles/{article_id}/business-value").json()
    assert state["status"] == "queued"


def test_get_recovers_an_orphan_row_left_by_a_dead_process(client):
    """进程重启留下的行由轮询的 GET 回收，用户不用等别人 POST。"""
    article_id = _add_article("a5")
    conn = get_db()
    stale = datetime.now(timezone.utc) - timedelta(seconds=bv_route.bv_runner.STALE_AFTER_SEC + 60)
    conn.execute(
        "UPDATE articles SET business_value_status='running', business_value_at=? WHERE id=?",
        (stale.isoformat(), article_id),
    )
    conn.commit()

    state = client.get(f"/api/articles/{article_id}/business-value").json()
    assert state["status"] == "failed"
    assert "中断" in (state["error"] or "")
    assert client.llm_calls["n"] == 0

    # 回收之后重新点一次就能真打分。
    assert client.post(f"/api/articles/{article_id}/business-value").json()["queued"] is True
    assert _wait(client, article_id)["status"] == "ready"


def test_two_concurrent_posts_score_the_article_once(client):
    article_id = _add_article("a6")
    results: list[dict] = []
    lock = threading.Lock()

    def _post() -> None:
        payload = client.post(f"/api/articles/{article_id}/business-value").json()
        with lock:
            results.append(payload)

    threads = [threading.Thread(target=_post) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # 最多只有一个请求真的排上队（另一个要么看到 running，要么看到缓存）。
    assert sum(1 for payload in results if payload["queued"]) <= 1
    assert _wait(client, article_id)["status"] == "ready"
    # 一次完整打分 = 取证 + 评分 + 审核 = 3 次模型调用。
    assert client.llm_calls["n"] == 3
