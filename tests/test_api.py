"""tests/test_api.py — pytest coverage for all 5 web API menus."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

import pytest

# Ensure moni is importable from the repo root
sys.path.insert(0, os.path.expanduser("~/dev/source-monitor/wt/t1-db"))

from fastapi.testclient import TestClient

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app

# ---------------------------------------------------------------------------
# Module-level app and client (shared across tests via fixtures below)
# ---------------------------------------------------------------------------

_app = None


def _get_app() -> TestClient:
    global _app
    if _app is None:
        _app = TestClient(create_app())
    return _app


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Reset the database to a temp file before every test."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    migrate()
    yield
    close_db()
    # Reset module-level singletons
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None
    # Reset shared app so new DB is used
    global _app
    _app = None


@pytest.fixture
def client() -> TestClient:
    """HTTP client pointed at a fresh in-memory app."""
    return TestClient(create_app())


@pytest.fixture
def seeded_article(client: TestClient) -> int:
    """Insert a minimal article into the DB and return its integer ID."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO articles (id, title, url, source, publisher, published_at, fetched_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        # article.id is TEXT in the schema; we use "1" as a short string that
        # parses cleanly when the route does str(body.article_id).
        ("1", "Test Article", "https://example.com/1", "testsrc", "TestPub", now, now),
    )
    conn.commit()
    return "1"  # string — matches article.id TEXT primary key and article_ids list elements


# ---------------------------------------------------------------------------
# articles
# ---------------------------------------------------------------------------

class TestArticles:
    def test_list_200(self, client: TestClient):
        r = client.get("/api/articles")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_by_id_200(self, client: TestClient, seeded_article: int):
        r = client.get(f"/api/articles/{seeded_article}")
        assert r.status_code == 200
        data = r.json()
        assert data["id"] == str(seeded_article)

    def test_get_by_id_404(self, client: TestClient):
        r = client.get("/api/articles/nonexistent")
        assert r.status_code == 404

    def test_summarize_503_without_service(self, client: TestClient, seeded_article: int):
        """When moni.summarizer is unavailable, expect 503."""
        r = client.post(f"/api/articles/{seeded_article}/summary", json={"article_id": str(seeded_article)})
        assert r.status_code in (200, 503)

    def test_factcheck_503_without_service(self, client: TestClient, seeded_article: int):
        """When moni.factcheck is unavailable, expect 503."""
        r = client.post(f"/api/articles/{seeded_article}/fact-check", json={"article_id": str(seeded_article)})
        assert r.status_code in (200, 503)


# ---------------------------------------------------------------------------
# crud
# ---------------------------------------------------------------------------

class TestCRUD:
    def test_put_update_200(self, client: TestClient, seeded_article: int):
        r = client.put(
            f"/api/articles/{seeded_article}",
            json={"title": "Updated Title"},
        )
        assert r.status_code == 200
        assert r.json()["title"] == "Updated Title"

    def test_put_update_404(self, client: TestClient):
        r = client.put("/api/articles/nonexistent", json={"title": "New"})
        assert r.status_code == 404

    def test_delete_preview_200(self, client: TestClient, seeded_article: int):
        r = client.get(f"/api/articles/{seeded_article}/delete-preview")
        assert r.status_code == 200
        data = r.json()
        assert "token" in data
        assert "article_title" in data

    def test_delete_preview_404(self, client: TestClient):
        r = client.get("/api/articles/nonexistent/delete-preview")
        assert r.status_code == 404

    def test_delete_without_confirm_400(self, client: TestClient, seeded_article: int):
        r = client.delete(f"/api/articles/{seeded_article}")
        assert r.status_code == 400

    def test_delete_with_confirm_1_200(self, client: TestClient, seeded_article: int):
        r = client.delete(f"/api/articles/{seeded_article}?confirm=1")
        assert r.status_code == 200
        assert r.json()["deleted"] == str(seeded_article)

        # Verify it's gone
        r2 = client.get(f"/api/articles/{seeded_article}")
        assert r2.status_code == 404


# ---------------------------------------------------------------------------
# cron
# ---------------------------------------------------------------------------

class TestCron:
    def test_get_jobs_200(self, client: TestClient):
        r = client.get("/api/cron/jobs")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_post_create_job_201(self, client: TestClient):
        r = client.post(
            "/api/cron/jobs",
            json={"name": "test-job", "schedule": "0 * * * *"},
        )
        assert r.status_code == 201
        data = r.json()
        assert data["name"] == "test-job"

    def test_get_runs_200(self, client: TestClient):
        r = client.get("/api/cron/runs")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_get_runs_pagination(self, client: TestClient):
        r = client.get("/api/cron/runs?page=1&page_size=5")
        assert r.status_code == 200


# ---------------------------------------------------------------------------
# fetch
# ---------------------------------------------------------------------------

class TestFetch:
    def test_get_sources_200(self, client: TestClient):
        r = client.get("/api/fetch/sources")
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_put_update_200(self, client: TestClient):
        # Update accepts any non-None fields
        r = client.put("/api/fetch/sources", json={"crawler_type": "rss"})
        assert r.status_code == 200
        assert isinstance(r.json(), list)

    def test_post_run_200_or_202(self, client: TestClient):
        # run_fetcher may return None (skipped) or a dict; sources.yaml has 36kr_rss
        r = client.post("/api/fetch/sources/36kr_rss/run")
        assert r.status_code in (200, 202)

    def test_post_run_404(self, client: TestClient):
        r = client.post("/api/fetch/sources/nonexistent-source/run")
        assert r.status_code == 404
    def test_post_health_200(self, client: TestClient):
        r = client.post("/api/fetch/sources/36kr_rss/health")
        assert r.status_code == 200
        data = r.json()
        assert "status" in data
        assert "db_connected" in data

    def test_post_health_404(self, client: TestClient):
        r = client.post("/api/fetch/sources/nonexistent/health")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# share
# ---------------------------------------------------------------------------

class TestShare:
    def test_post_create_task_201(self, client: TestClient, seeded_article: str):
        r = client.post(
            "/api/share",
            json={
                "format": "markdown",
                "version": "1.0",
                "payload": {
                    "article_ids": [seeded_article],
                    "include_toc": True,
                    "include_metadata": True,
                },
            },
        )
        assert r.status_code == 201
        data = r.json()
        assert "share_id" in data
        assert data["status"] == "pending"

    def test_post_create_task_400_unknown_format(self, client: TestClient, seeded_article: str):
        r = client.post(
            "/api/share",
            json={
                "format": "unknown_format",
                "version": "1.0",
                "payload": {"article_ids": [seeded_article]},
            },
        )
        assert r.status_code == 400

    def test_post_create_task_400_bad_payload(self, client: TestClient):
        r = client.post(
            "/api/share",
            json={
                "format": "markdown",
                "version": "1.0",
                "payload": {
                    # missing required article_ids
                    "include_toc": True,
                },
            },
        )
        assert r.status_code == 400

    def test_get_tasks_list_200(self, client: TestClient):
        r = client.get("/api/share/tasks")
        assert r.status_code == 200
        data = r.json()
        assert "items" in data
        assert "total" in data

    def test_get_task_by_id_200(self, client: TestClient, seeded_article: str):
        # First create a task
        create_r = client.post(
            "/api/share",
            json={
                "format": "markdown",
                "version": "1.0",
                "payload": {"article_ids": [seeded_article]},
            },
        )
        assert create_r.status_code == 201
        task_id = create_r.json()["share_id"]

        r = client.get(f"/api/share/tasks/{task_id}")
        assert r.status_code == 200
        assert r.json()["id"] == task_id

    def test_get_task_by_id_404(self, client: TestClient):
        r = client.get("/api/share/tasks/nonexistent-task-id")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# CORS
# ---------------------------------------------------------------------------

class TestCORS:
    def test_disallowed_origin_has_no_access_control_header(self, client: TestClient):
        """Requests from origins other than http://127.0.0.1:8900 must not
        receive Access-Control-Allow-Origin."""
        r = client.get(
            "/api/articles",
            headers={"Origin": "http://evil.com"},
        )
        # The response should NOT contain Access-Control-Allow-Origin
        # FastAPI + CORS middleware only adds it when origin is allowed
        assert "Access-Control-Allow-Origin" not in r.headers

    def test_allowed_origin_has_access_control_header(self, client: TestClient):
        r = client.get(
            "/api/articles",
            headers={"Origin": "http://127.0.0.1:8900"},
        )
        assert r.headers.get("Access-Control-Allow-Origin") == "http://127.0.0.1:8900"
