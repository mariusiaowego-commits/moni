"""API tests for business directions (Sprint 67, S2)."""
from __future__ import annotations

import json
import threading

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.scoring import direction_store
from tests.test_scoring_direction import _payload


@pytest.fixture
def client(tmp_path, monkeypatch):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    from moni.web.server import create_app

    from moni.web.routes import directions as directions_route

    release_model = threading.Event()
    directions_route._release_model = release_model

    def fake_model_call(_prompt: str) -> str:
        release_model.wait(timeout=5)
        return json.dumps(_payload(), ensure_ascii=False)

    monkeypatch.setattr(
        directions_route,
        "_model_call",
        fake_model_call,
    )
    with TestClient(create_app()) as test_client:
        # The route caches a runner; reset it so each test gets the
        # monkeypatched model call above.
        directions_route._runner = None
        yield test_client
        release_model.set()
        if directions_route._runner is not None:
            directions_route._runner.wait_idle(timeout=5)
        directions_route._runner = None
        directions_route._release_model = None
    close_db()


def _create(client: TestClient, label="微信生态", brief="微信里的银行动作") -> dict:
    response = client.post("/api/directions", json={"label": label, "brief": brief})
    assert response.status_code == 201, response.text
    return response.json()


def test_direction_crud(client: TestClient):
    created = _create(client)
    assert created["label"] == "微信生态"
    assert created["enabled"] is True
    assert created["usable"] is False

    listed = client.get("/api/directions")
    assert listed.status_code == 200
    assert [item["id"] for item in listed.json()] == [created["id"]]

    patched = client.patch(
        f"/api/directions/{created['id']}",
        json={"enabled": False},
    )
    assert patched.status_code == 200
    assert patched.json()["enabled"] is False

    deleted = client.delete(f"/api/directions/{created['id']}")
    assert deleted.status_code == 204
    assert client.get("/api/directions").json() == []


def test_direction_validation(client: TestClient):
    response = client.post(
        "/api/directions", json={"label": "超过六个字的方向", "brief": "说明"}
    )
    assert response.status_code == 422

    response = client.post(
        "/api/directions", json={"label": "微信生态", "brief": "长" * 201}
    )
    assert response.status_code == 422


def test_update_expires_research_snapshot(client: TestClient):
    created = _create(client)
    conn = get_db()
    research, error = direction_store.start_research(conn, created["id"])
    assert error is None
    direction_store.finish_research_success(
        conn,
        direction_id=created["id"],
        research_version=research["research_version"],
        raw_research=_payload(),
        model="test",
    )

    response = client.patch(
        f"/api/directions/{created['id']}", json={"brief": "公众号里的银行动作"}
    )
    assert response.status_code == 200
    assert response.json()["usable"] is False

    rows = conn.execute(
        "SELECT status FROM bv_direction_research WHERE direction_id=?",
        (created["id"],),
    ).fetchall()
    assert [row[0] for row in rows] == ["expired"]


def test_research_action_returns_202_and_conflicts_on_duplicate(client: TestClient):
    created = _create(client)
    response = client.post(f"/api/directions/{created['id']}/research")
    assert response.status_code == 202
    body = response.json()
    assert body["status"] == "running"
    assert body["research_version"] == 1

    duplicate = client.post(f"/api/directions/{created['id']}/research")
    assert duplicate.status_code == 409

    research = client.get(f"/api/directions/{created['id']}/research")
    assert research.status_code == 200
    # The fake model returns a valid payload, so the run may already have
    # finished; both "running" and "passed" are legal post-submit states.
    assert research.json()["status"] in {"running", "passed"}
    assert research.json()["prompt_id"] == "bv_direction_research_v1"


def test_research_action_reaches_passed_after_release(client: TestClient):
    from moni.web.routes import directions as directions_route

    created = _create(client)
    response = client.post(f"/api/directions/{created['id']}/research")
    assert response.status_code == 202

    assert directions_route._release_model is not None
    directions_route._release_model.set()
    assert directions_route._runner is not None
    assert directions_route._runner.wait_idle(timeout=5)

    research = client.get(f"/api/directions/{created['id']}/research")
    assert research.status_code == 200
    assert research.json()["status"] == "passed"


def test_research_not_found(client: TestClient):
    assert client.get("/api/directions/999/research").status_code == 404
    response = client.post("/api/directions/999/research")
    assert response.status_code == 404


def test_direction_payload_does_not_expose_rubric(client: TestClient):
    created = _create(client)
    conn = get_db()
    research, _ = direction_store.start_research(conn, created["id"])
    direction_store.finish_research_success(
        conn,
        direction_id=created["id"],
        research_version=research["research_version"],
        raw_research=_payload(),
        model="test",
    )

    listed = client.get("/api/directions")
    assert "rubric_json" not in json.dumps(listed.json())
    detail = client.get(f"/api/directions/{created['id']}/research")
    assert "rubric_json" not in detail.text


def test_duplicate_label_returns_409(client: TestClient):
    _create(client, label="微信生态")

    duplicate = client.post(
        "/api/directions", json={"label": "微信生态", "brief": "另一份说明"}
    )
    assert duplicate.status_code == 409
    assert "已存在" in duplicate.json()["message"]

    # Whitespace around the name must not sneak past the check.
    padded = client.post(
        "/api/directions", json={"label": "  微信生态  ", "brief": "说明"}
    )
    assert padded.status_code == 409


def test_rename_to_existing_label_returns_409(client: TestClient):
    first = _create(client, label="微信生态")
    second = _create(client, label="短视频")

    renamed = client.patch(
        f"/api/directions/{second['id']}", json={"label": "微信生态"}
    )
    assert renamed.status_code == 409
    assert first["label"] != second["label"]

    # Renaming to its own name is fine.
    same = client.patch(f"/api/directions/{first['id']}", json={"label": " 微信生态 "})
    assert same.status_code == 200
    assert same.json()["label"] == "微信生态"


def test_disabled_direction_cannot_start_research(client: TestClient):
    created = _create(client)
    client.patch(f"/api/directions/{created['id']}", json={"enabled": False})

    response = client.post(f"/api/directions/{created['id']}/research")
    assert response.status_code == 409
    assert "停用" in response.json()["message"]


def test_banned_label_returns_400(client: TestClient):
    response = client.post("/api/directions", json={"label": "深度洞察", "brief": "说明"})
    assert response.status_code == 400
    assert "禁用词" in response.json()["message"]


def test_missing_direction_returns_404(client: TestClient):
    assert client.get("/api/directions/999").status_code == 404
    assert client.patch("/api/directions/999", json={"enabled": True}).status_code == 404
    assert client.delete("/api/directions/999").status_code == 404


def test_cross_origin_write_is_rejected(client: TestClient):
    response = client.post(
        "/api/directions",
        json={"label": "微信生态", "brief": "说明"},
        headers={"Origin": "https://malicious-site.example"},
    )
    assert response.status_code == 403


def test_create_and_list_return_research_guidance(client: TestClient):
    response = client.post(
        "/api/directions",
        json={
            "label": "投放",
            "brief": "看投放数据能不能变成获客",
            "research_guidance": "只认正文里的投放数字",
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["research_guidance"] == "只认正文里的投放数字"
    listed = client.get("/api/directions")
    assert listed.json()[0]["research_guidance"] == "只认正文里的投放数字"


def test_over_long_guidance_is_rejected(client: TestClient):
    response = client.post(
        "/api/directions",
        json={"label": "投放", "brief": "说明", "research_guidance": "好" * 801},
    )
    assert response.status_code == 422


def test_patch_guidance_expires_passed_research(client: TestClient):
    created = _create(client)
    conn = get_db()
    research, error = direction_store.start_research(conn, created["id"])
    assert error is None and research is not None
    direction_store.finish_research_success(
        conn,
        direction_id=created["id"],
        research_version=research["research_version"],
        raw_research=_payload(),
        model="test",
    )

    response = client.patch(
        f"/api/directions/{created['id']}",
        json={"research_guidance": "只认投放数字"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["research_guidance"] == "只认投放数字"
    assert body["research_invalidated"] is True
    assert body["usable"] is False
    rows = conn.execute(
        "SELECT status FROM bv_direction_research WHERE direction_id=?",
        (created["id"],),
    ).fetchall()
    assert [row[0] for row in rows] == ["expired"]


def test_patch_same_guidance_keeps_the_passed_research(client: TestClient):
    created = client.post(
        "/api/directions",
        json={"label": "投放", "brief": "看投放数据", "research_guidance": "同一份"},
    )
    assert created.status_code == 201, created.text
    direction_id = created.json()["id"]
    conn = get_db()
    research, error = direction_store.start_research(conn, direction_id)
    assert error is None and research is not None
    direction_store.finish_research_success(
        conn,
        direction_id=direction_id,
        research_version=research["research_version"],
        raw_research=_payload(),
        model="test",
    )

    response = client.patch(
        f"/api/directions/{direction_id}",
        json={"research_guidance": "同一份"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["research_invalidated"] is False
    assert body["usable"] is True


def test_draft_returns_the_model_draft_and_saves_nothing(client: TestClient, monkeypatch):
    from moni.web.routes import directions as directions_route

    def fake(prompt: str) -> str:
        assert "投放数据" in prompt
        return json.dumps(
            {
                "label": "投放",
                "brief": "看投放数据能不能变成获客",
                "research_guidance": "只认正文里的投放数字",
            },
            ensure_ascii=False,
        )

    monkeypatch.setattr(directions_route, "_model_call", fake)
    response = client.post("/api/directions/draft", json={"idea": "投放数据"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["label"] == "投放"
    assert body["brief"] == "看投放数据能不能变成获客"
    assert body["research_guidance"] == "只认正文里的投放数字"
    assert client.get("/api/directions").json() == []


def test_draft_rejects_unusable_output(client: TestClient, monkeypatch):
    from moni.web.routes import directions as directions_route

    monkeypatch.setattr(directions_route, "_model_call", lambda _prompt: "not json")
    response = client.post("/api/directions/draft", json={"idea": "投放数据"})
    assert response.status_code == 400
    assert "草稿不可用" in response.json()["message"]


def test_draft_reports_a_model_failure(client: TestClient, monkeypatch):
    from moni.web.routes import directions as directions_route

    def fake(_prompt: str) -> str:
        raise RuntimeError("timed out after 30 seconds")

    monkeypatch.setattr(directions_route, "_model_call", fake)
    response = client.post("/api/directions/draft", json={"idea": "投放数据"})
    assert response.status_code == 400
    assert "生成草稿失败" in response.json()["message"]
    assert "超时" in response.json()["message"]


def test_blank_idea_is_rejected(client: TestClient):
    response = client.post("/api/directions/draft", json={"idea": "   "})
    assert response.status_code == 422


def test_patching_a_direction_without_research_does_not_claim_a_was_invalidated(client: TestClient):
    """A direction that never had a research has nothing to invalidate."""
    created = _create(client)
    response = client.patch(
        f"/api/directions/{created['id']}", json={"brief": "公众号里的银行动作"}
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["usable"] is False
    assert body["research_invalidated"] is False
