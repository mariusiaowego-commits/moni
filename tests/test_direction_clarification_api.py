"""API tests for the direction clarification round (sprint-74)."""
from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, set_db_path
from moni.db.migrate import migrate
from tests.test_scoring_direction import _payload

PERSPECTIVE = "银行新媒体渠道从业者，想知道哪些内容能变成公众号选题"


@pytest.fixture
def client(tmp_path, monkeypatch):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    from moni.web.routes import directions as directions_route
    from moni.web.server import create_app

    def fake_model_call(prompt: str) -> str:
        if "澄清" in prompt:
            return json.dumps(
                {"sufficient": False, "questions": ["更在意获客还是合规？"]},
                ensure_ascii=False,
            )
        return json.dumps(_payload(), ensure_ascii=False)

    monkeypatch.setattr(directions_route, "_model_call", fake_model_call)
    with TestClient(create_app()) as test_client:
        directions_route._runner = None
        directions_route._clarification_runner = None
        yield test_client
        if directions_route._runner is not None:
            directions_route._runner.wait_idle(timeout=5)
        if directions_route._clarification_runner is not None:
            directions_route._clarification_runner.wait_idle(timeout=5)
        directions_route._runner = None
        directions_route._clarification_runner = None
    close_db()


def _create(client: TestClient, perspective=PERSPECTIVE) -> dict:
    response = client.post(
        "/api/directions",
        json={"label": "微信生态", "brief": "微信里的银行动作", "reader_perspective": perspective},
    )
    assert response.status_code == 201, response.text
    return response.json()


def test_direction_carries_the_reader_perspective(client: TestClient):
    created = _create(client)
    assert created["reader_perspective"] == PERSPECTIVE


def test_clarification_round_asks_questions_and_stores_answers(client: TestClient):
    created = _create(client)
    direction_id = created["id"]

    response = client.post(f"/api/directions/{direction_id}/clarification")
    assert response.status_code == 202, response.text
    assert response.json()["status"] == "running"

    for _ in range(50):
        summary = client.get(f"/api/directions/{direction_id}/clarification").json()
        if summary["status"] != "running":
            break
    assert summary["status"] == "passed"
    assert summary["questions"] == ["更在意获客还是合规？"]

    saved = client.put(
        f"/api/directions/{direction_id}/clarification/answers",
        json={"answers": ["两者都要，先获客"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["answers"] == ["两者都要，先获客"]


def test_research_is_refused_while_questions_are_unanswered(client: TestClient):
    created = _create(client)
    direction_id = created["id"]
    client.post(f"/api/directions/{direction_id}/clarification")
    for _ in range(50):
        summary = client.get(f"/api/directions/{direction_id}/clarification").json()
        if summary["status"] != "running":
            break
    assert summary["status"] == "passed"

    response = client.post(f"/api/directions/{direction_id}/research")
    assert response.status_code == 409
    assert "澄清" in response.json()["message"]


def test_research_runs_after_the_questions_are_answered(client: TestClient):
    created = _create(client)
    direction_id = created["id"]
    client.post(f"/api/directions/{direction_id}/clarification")
    for _ in range(50):
        summary = client.get(f"/api/directions/{direction_id}/clarification").json()
        if summary["status"] != "running":
            break
    client.put(
        f"/api/directions/{direction_id}/clarification/answers",
        json={"answers": ["先获客"]},
    )

    response = client.post(f"/api/directions/{direction_id}/research")
    assert response.status_code == 202, response.text


def test_clarification_needs_a_perspective(client: TestClient):
    created = _create(client, perspective="")
    response = client.post(f"/api/directions/{created['id']}/clarification")
    assert response.status_code == 409
    assert "读者视角" in response.json()["message"]


def test_clarification_of_an_unknown_direction_is_404(client: TestClient):
    assert client.get("/api/directions/999/clarification").status_code == 404
