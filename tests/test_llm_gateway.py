"""网关视角的 LLM 设置：只读快照 + provider 测试的真实语义。

v1.0.72：设置页改成「网关 / 用量统计」，模型映射收进网关页的
「高级：explicit 兜底」折叠面板。本文件覆盖
① ``GET /api/llm/gateway`` 返回 profile / 网关状态 / 可用模型；
② 网关不可达时仍然 200（页面要能显示原因）；
③ provider 连通测试在 ``profile`` 模式下测的是 profile 当前模型，不是该 provider 的模型。
"""
from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import get_db


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """每个测试用独立的临时库。"""
    from moni.db.connection import close_db, set_db_path
    from moni.db.migrate import migrate

    monkeypatch.setenv("MONI_QUEUE_DIR", str(tmp_path / "queue"))
    set_db_path(tmp_path / "test.db")
    migrate()
    yield
    close_db()


@pytest.fixture
def client() -> TestClient:
    from moni.web.server import create_app

    return TestClient(create_app())


@pytest.fixture
def fake_hermes_home(tmp_path, monkeypatch):
    """一个假的 hermes home：只有 moni profile 的 config.yaml。"""
    home = tmp_path / "hermes"
    profile_dir = home / "profiles" / "moni"
    profile_dir.mkdir(parents=True)
    (profile_dir / "config.yaml").write_text(
        "model:\n"
        "  default: group/tq-s-select\n"
        "  provider: magpie\n"
        "providers:\n"
        "  magpie:\n"
        "    base_url: http://127.0.0.1:3425/v1\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("HERMES_HOME", str(home))
    monkeypatch.setenv("MONI_HERMES_PROFILE", "moni")
    monkeypatch.delenv("HERMES_PROFILE", raising=False)
    return home


def _fake_gateway(monkeypatch, payload: dict | None = None, error: Exception | None = None):
    """把 httpx.get 换成固定返回。"""
    import moni.llm.gateway as gateway_mod

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return payload or {"data": []}

    def fake_get(url, *args, **kwargs):
        if error is not None:
            raise error
        return _Resp()

    monkeypatch.setattr(gateway_mod.httpx, "get", fake_get)


class TestGatewayModule:
    def test_profile_model_reads_hermes_profile(self, fake_hermes_home):
        from moni.llm.gateway import profile_model

        info = profile_model()
        assert info["profile"] == "moni"
        assert info["provider"] == "magpie"
        assert info["default_model"] == "group/tq-s-select"
        assert info["base_url"] == "http://127.0.0.1:3425/v1"
        assert info["config_found"] is True

    def test_profile_model_missing_file_is_not_fatal(self, tmp_path, monkeypatch):
        from moni.llm.gateway import profile_model

        monkeypatch.setenv("HERMES_HOME", str(tmp_path / "nope"))
        info = profile_model()
        assert info["default_model"] == ""
        assert info["config_found"] is False

    def test_probe_gateway_lists_groups(self, monkeypatch):
        from moni.llm.gateway import probe_gateway

        _fake_gateway(
            monkeypatch,
            payload={
                "data": [
                    {"id": "group/tq-s-select", "magpie_label": "tq-s-select · routing group"},
                    {"id": "minimax-cn/MiniMax-M3.1-Flash", "display_name": "MiniMax"},
                    {"id": ""},
                ]
            },
        )
        gw = probe_gateway("http://127.0.0.1:3425/v1")
        assert gw["reachable"] is True
        assert gw["model_count"] == 2
        assert gw["groups"] == ["group/tq-s-select"]
        assert gw["models"][0]["is_group"] is True
        assert gw["models"][1]["is_group"] is False

    def test_probe_gateway_unreachable_reports_reason(self, monkeypatch):
        from moni.llm.gateway import probe_gateway

        _fake_gateway(monkeypatch, error=RuntimeError("connection refused"))
        gw = probe_gateway("http://127.0.0.1:3425/v1")
        assert gw["reachable"] is False
        assert "connection refused" in gw["error"]
        assert gw["models"] == []


class TestGatewayRoute:
    def test_gateway_route_returns_snapshot(self, client: TestClient, fake_hermes_home, monkeypatch):
        _fake_gateway(monkeypatch, payload={"data": [{"id": "group/tq-s-select"}]})
        r = client.get("/api/llm/gateway")
        assert r.status_code == 200
        data = r.json()
        assert data["profile"]["default_model"] == "group/tq-s-select"
        assert data["gateway"]["reachable"] is True
        assert data["gateway"]["model_count"] == 1
        assert data["mode"] in ("profile", "explicit")

    def test_gateway_route_survives_unreachable_gateway(self, client: TestClient, fake_hermes_home, monkeypatch):
        _fake_gateway(monkeypatch, error=RuntimeError("boom"))
        r = client.get("/api/llm/gateway")
        assert r.status_code == 200
        data = r.json()
        assert data["gateway"]["reachable"] is False
        assert "boom" in data["gateway"]["error"]


class TestProviderTestSemantics:
    """profile 模式下 provider 连通测试必须说清楚「测的是谁」。"""

    def _seed_provider(self):
        from moni.db.seed_llm import seed as seed_llm

        seed_llm()
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO llm_settings (key, value) "
            "VALUES ('llm_model_mode', 'profile')"
        )
        conn.commit()

    def test_profile_mode_tests_profile_model(self, client: TestClient, fake_hermes_home, monkeypatch):
        self._seed_provider()
        captured = {}

        def fake_call_llm(provider, model, prompt, **kwargs):
            captured.update({"provider": provider, "model": model})
            return "ok"

        import moni.llm.client as client_mod

        monkeypatch.setattr(client_mod, "call_llm", fake_call_llm)

        conn = get_db()
        pid = conn.execute("SELECT id FROM llm_providers LIMIT 1").fetchone()["id"]
        r = client.post(f"/api/llm/providers/{pid}/test")
        assert r.status_code == 200
        data = r.json()
        assert data["ok"] is True
        assert data["tested"] == "profile"
        assert data["model"] == "group/tq-s-select"
        assert captured == {"provider": "", "model": ""}

    def test_explicit_mode_tests_provider_model(self, client: TestClient, fake_hermes_home, monkeypatch):
        from moni.db.seed_llm import seed as seed_llm

        seed_llm()
        conn = get_db()
        conn.execute(
            "INSERT OR REPLACE INTO llm_settings (key, value) "
            "VALUES ('llm_model_mode', 'explicit')"
        )
        conn.commit()
        captured = {}

        def fake_call_llm(provider, model, prompt, **kwargs):
            captured.update({"provider": provider, "model": model})
            return "ok"

        import moni.llm.client as client_mod

        monkeypatch.setattr(client_mod, "call_llm", fake_call_llm)

        conn = get_db()
        pid = conn.execute("SELECT id FROM llm_providers LIMIT 1").fetchone()["id"]
        r = client.post(f"/api/llm/providers/{pid}/test")
        assert r.status_code == 200
        data = r.json()
        assert data["tested"] == "provider"
        assert captured["provider"]
        assert captured["model"]
