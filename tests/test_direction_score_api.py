"""API tests for direction scoring (Sprint 67, S3)."""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.scoring import direction_store, rubric
from moni.web.routes import direction_scores as scores_route
from tests.test_scoring_bv import BODY, make_llm
from tests.test_scoring_direction import _payload


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

    llm = make_llm(scores=_scores(7))
    monkeypatch.setattr(scores_route, "_llm_call", llm)
    with TestClient(create_app()) as test_client:
        scores_route._runner = None
        yield test_client
        if scores_route._runner is not None:
            scores_route._runner.wait_idle(timeout=30)
        scores_route._runner = None
    close_db()


def _add_articles(count: int, body: str = BODY) -> list[str]:
    conn = get_db()
    ids = []
    for index in range(count):
        article_id = f"a{index + 1}"
        ids.append(article_id)
        conn.execute(
            "INSERT INTO articles (id, title, url, source, publisher, published_at, "
            "fetched_at, raw_content) VALUES (?,?,?,?,?,?,?,?)",
            (article_id, f"标题{index}", f"https://e.com/{index}", "s", "p",
             "2024-05-10", "2024-05-11", body),
        )
    conn.commit()
    return ids


def _usable_direction(label="微信生态") -> int:
    conn = get_db()
    direction_id, error = direction_store.create_direction(
        conn, label, "微信里的银行动作"
    )
    assert error is None
    research, error = direction_store.start_research(conn, direction_id)
    assert error is None
    direction_store.finish_research_success(
        conn,
        direction_id=direction_id,
        research_version=research["research_version"],
        raw_research=_payload(),
        model="test",
    )
    return direction_id


def test_unknown_direction_returns_404(client: TestClient):
    assert client.get("/api/directions/999/scores?article_ids=a1").status_code == 404
    response = client.post("/api/directions/999/scores", json={"article_ids": ["a1"]})
    assert response.status_code == 404


def test_disabled_direction_returns_409(client: TestClient):
    conn = get_db()
    direction_id, _ = direction_store.create_direction(conn, "短视频", "短视频里的动作")
    direction_store.update_direction(conn, direction_id, enabled=False)
    response = client.get(f"/api/directions/{direction_id}/scores?article_ids=a1")
    assert response.status_code == 409
    assert "停用" in response.json()["message"]


def test_direction_without_research_returns_409(client: TestClient):
    conn = get_db()
    direction_id, _ = direction_store.create_direction(conn, "短视频", "短视频里的动作")
    response = client.get(f"/api/directions/{direction_id}/scores?article_ids=a1")
    assert response.status_code == 409
    assert "研究" in response.json()["message"]


def test_empty_and_oversized_requests_return_400(client: TestClient):
    direction_id = _usable_direction()
    response = client.get(f"/api/directions/{direction_id}/scores?article_ids=")
    assert response.status_code == 400
    response = client.post(
        f"/api/directions/{direction_id}/scores", json={"article_ids": []}
    )
    assert response.status_code == 400
    ids = [f"x{i}" for i in range(51)]
    response = client.post(
        f"/api/directions/{direction_id}/scores", json={"article_ids": ids}
    )
    assert response.status_code == 400
    assert "50" in response.json()["message"]


def test_batch_over_twelve_unscored_returns_400(client: TestClient):
    direction_id = _usable_direction()
    ids = _add_articles(13)
    response = client.post(
        f"/api/directions/{direction_id}/scores", json={"article_ids": ids}
    )
    assert response.status_code == 400
    assert "12" in response.json()["message"]


def test_post_queues_then_scores_then_caches(client: TestClient):
    direction_id = _usable_direction()
    ids = _add_articles(2)

    response = client.post(
        f"/api/directions/{direction_id}/scores", json={"article_ids": ids}
    )
    assert response.status_code == 202, response.text
    body = response.json()
    assert sorted(body["queued"]) == sorted(ids)
    assert body["cached"] == []
    assert body["rejected"] == []
    assert body["research_version"] == 1
    assert body["rubric_id"] == "bv_direction_v1"

    assert scores_route._runner is not None
    assert scores_route._runner.wait_idle(timeout=30)

    response = client.get(
        f"/api/directions/{direction_id}/scores?article_ids={','.join(ids)}"
    )
    assert response.status_code == 200
    scores = response.json()["scores"]
    assert set(scores) == set(ids)
    for item in scores.values():
        assert item["status"] == "scored"
        assert item["score"] == 70
        assert item["stale"] is False
        assert item["breakdown"]["dimensions"]
        for dim in item["breakdown"]["dimensions"]:
            assert set(dim) <= {"key", "label", "weight", "score", "reason"}

    again = client.post(
        f"/api/directions/{direction_id}/scores", json={"article_ids": ids}
    )
    assert again.status_code == 202
    assert again.json()["queued"] == []
    assert sorted(again.json()["cached"]) == sorted(ids)


def test_missing_article_is_rejected(client: TestClient):
    direction_id = _usable_direction()
    ids = _add_articles(1)
    response = client.post(
        f"/api/directions/{direction_id}/scores",
        json={"article_ids": [*ids, "ghost"]},
    )
    assert response.status_code == 202
    rejected = response.json()["rejected"]
    assert rejected == [{"id": "ghost", "reason": "文章不存在"}]


def test_response_never_exposes_rubric(client: TestClient):
    direction_id = _usable_direction()
    ids = _add_articles(1)
    client.post(f"/api/directions/{direction_id}/scores", json={"article_ids": ids})
    assert scores_route._runner is not None
    assert scores_route._runner.wait_idle(timeout=30)
    response = client.get(
        f"/api/directions/{direction_id}/scores?article_ids={','.join(ids)}"
    )
    assert "rubric_json" not in response.text
    assert "rubric_json" not in json.dumps(response.json())
