"""HTTP surface of the cleaning page (``/api/cleaning/*``)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app


@pytest.fixture
def client(tmp_path):
    set_db_path(tmp_path / "test.db")
    migrate()
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO sources (id, name, display_name, source_type, url,
                             fetch_interval_min, is_active, created_at, updated_at)
        VALUES ('src-q', 'qbitai', 'qbitai', 'rss', 'https://qbitai.com',
                30, 1, ?, ?)
        """,
        (now, now),
    )
    conn.execute(
        """
        INSERT INTO articles (id, title, url, source, fetched_at, raw_content)
        VALUES ('a1', '标题', 'https://e.com/1', 'qbitai', ?, ?)
        """,
        (now, "正文。点击关注公众号「x」了解更多"),
    )
    conn.commit()

    yield TestClient(create_app())

    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod

    conn_mod._CON = None  # type: ignore[attr-defined]
    conn_mod._DB_PATH = None  # type: ignore[attr-defined]
    mig_mod._CON = None  # type: ignore[attr-defined]


class TestEndpoints:
    def test_overview(self, client):
        body = client.get("/api/cleaning/overview").json()
        assert body["total"] == 1
        assert body["pending"] == 1
        assert body["cleaner"] == "clean_v1"
        assert body["sources"][0]["source"] == "qbitai"

    def test_preview_then_apply(self, client):
        preview = client.post("/api/cleaning/preview")
        assert preview.status_code == 200
        assert preview.json()["mode"] == "preview"
        assert preview.json()["changed"] == 1

        applied = client.post("/api/cleaning/apply")
        assert applied.status_code == 200
        assert applied.json()["mode"] == "apply"
        assert applied.json()["changed"] == 1

        again = client.post("/api/cleaning/apply").json()
        assert again["changed"] == 0

    def test_runs_list_and_detail(self, client):
        run_id = client.post("/api/cleaning/preview").json()["id"]
        runs = client.get("/api/cleaning/runs").json()
        assert runs[0]["id"] == run_id
        assert client.get(f"/api/cleaning/runs/{run_id}").json()["id"] == run_id

    def test_unknown_run_is_404(self, client):
        assert client.get("/api/cleaning/runs/nope").status_code == 404

    def test_report_markdown(self, client):
        run_id = client.post("/api/cleaning/apply").json()["id"]
        response = client.get(f"/api/cleaning/runs/{run_id}/report.md")
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/markdown")
        assert "数据清洗报告" in response.text

    def test_sources_list(self, client):
        rows = client.get("/api/cleaning/sources").json()
        assert rows[0]["contract"] == "snippet"
        assert rows[0]["total"] == 1

    def test_set_contract(self, client):
        response = client.put(
            "/api/cleaning/sources/qbitai/contract",
            json={"contract": "title_only"},
        )
        assert response.status_code == 200
        assert response.json()["contract"] == "title_only"
        assert client.get("/api/cleaning/overview").json()["sources"][0]["contract"] == "title_only"

    def test_unknown_contract_is_400(self, client):
        response = client.put(
            "/api/cleaning/sources/qbitai/contract",
            json={"contract": "nonsense"},
        )
        assert response.status_code == 400
        body = response.json()
        assert body["code"] == "bad_request"
        assert body["details"]["allowed"] == [
            "full_text", "snippet", "short_form", "title_only",
        ]

    def test_unknown_source_is_404(self, client):
        response = client.put(
            "/api/cleaning/sources/ghost/contract",
            json={"contract": "full_text"},
        )
        assert response.status_code == 404
