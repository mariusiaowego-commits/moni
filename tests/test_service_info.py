"""Sprint 52: 顶部服务胶囊的 admin 浮层数据与结构。"""
from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

STATIC = Path(__file__).resolve().parents[1] / "moni" / "web" / "static"


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    from moni.web.routes import service as svc

    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    get_db().commit()
    monkeypatch.setattr(svc, "PIDFILE", tmp_path / ".web.pid")
    svc._info_cache.clear()
    monkeypatch.setattr(svc, "_info_cached_at", 0.0)
    yield
    svc._info_cache.clear()
    close_db()
    import moni.db.connection as conn_mod

    conn_mod._CON = None
    conn_mod._DB_PATH = None


@pytest.fixture
def client() -> TestClient:
    from moni.web.server import create_app

    return TestClient(create_app())


EXPECTED_KEYS = {
    "running",
    "pid",
    "port",
    "uptime_sec",
    "source",
    "version",
    "commit",
    "commits_ahead",
    "branch",
    "worktree_dirty",
    "schema_version",
    "db_name",
    "db_size_mb",
    "articles",
    "jobs_pending",
    "jobs_running",
    "llm_mode",
    "last_fetch_at",
}


def test_info_exposes_every_panel_field(client: TestClient):
    body = client.get("/api/service/info?force=true").json()
    assert EXPECTED_KEYS <= set(body)
    assert body["port"] == 8900
    assert body["schema_version"] == 44
    assert body["articles"] == 0
    assert body["jobs_pending"] == 0
    assert body["jobs_running"] == 0


def test_info_never_leaks_absolute_db_path(client: TestClient):
    body = client.get("/api/service/info?force=true").json()
    assert body["db_name"] == "news.db"
    assert "/" not in body["db_name"]


def test_info_degrades_when_git_is_unavailable(client: TestClient, monkeypatch):
    from moni.web.routes import service as svc

    monkeypatch.setattr(svc, "_git", lambda *args, **kwargs: None)
    body = client.get("/api/service/info?force=true").json()
    assert body["version"] is None
    assert body["commit"] is None
    assert body["branch"] is None
    assert body["worktree_dirty"] is None
    # 数据段仍然可用，面板不会因为缺 git 变空
    assert body["schema_version"] == 44


def test_clean_worktree_reports_false_not_null(client: TestClient, monkeypatch):
    """`git status --porcelain` prints nothing on a clean tree — that is False, not unknown."""
    from moni.web.routes import service as svc

    def fake_git(*args, allow_empty=False):
        if args[0] == "status":
            assert allow_empty, "status --porcelain must opt into empty output"
            return ""
        return None

    monkeypatch.setattr(svc, "_git", fake_git)
    body = client.get("/api/service/info?force=true").json()
    assert body["worktree_dirty"] is False


def test_info_is_cached_and_force_bypasses_it(client: TestClient, monkeypatch):
    from moni.web.routes import service as svc

    calls = {"n": 0}
    real_git = svc._git

    def counting_git(*args, **kwargs):
        calls["n"] += 1
        return real_git(*args, **kwargs)

    monkeypatch.setattr(svc, "_git", counting_git)
    client.get("/api/service/info?force=true")
    first = calls["n"]
    client.get("/api/service/info")
    client.get("/api/service/info")
    assert calls["n"] == first, "缓存期内不应再跑 git"
    client.get("/api/service/info?force=true")
    assert calls["n"] > first, "force=true 必须重新取"


def test_panel_markup_and_helpers_exist():
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    assert 'class="svc-panel"' in html
    assert 'class="svc-rows"' in html
    assert "loadServiceInfo()" in html
    assert "serviceVersionLabel()" in html
    js = (STATIC / "js" / "app.js").read_text(encoding="utf-8")
    assert "loadServiceInfo" in js
    assert "serviceUptimeLabel" in js
    css = (STATIC / "css" / "main.css").read_text(encoding="utf-8")
    assert ".svc-panel" in css
    assert ".svc-wrap:hover .svc-panel" in css
