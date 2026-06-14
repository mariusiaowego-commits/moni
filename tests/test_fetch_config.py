"""tests/test_fetch_config.py — tests for fetch_config API endpoints."""
from __future__ import annotations

import json
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.expanduser("~/dev/source-monitor"))

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.web.server import create_app

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def fresh_db(tmp_path):
    """Reset the database to a temp file before every test."""
    db_path = tmp_path / "test.db"
    set_db_path(db_path)
    migrate()
    yield
    close_db()
    import moni.db.connection as conn_mod
    import moni.db.migrate as mig_mod
    conn_mod._CON = None
    conn_mod._DB_PATH = None
    mig_mod._CON = None


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _seed_bestblogs_source(conn):
    """Insert a bestblogs source with fetch_config."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT OR REPLACE INTO sources (id, name, display_name, source_type, url,
                             fetch_interval_min, is_active, fetch_config,
                             created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "src-bestblogs", "bestblogs", "BestBlogs", "rss",
            "https://example.com/opml.xml", 120, 1,
            json.dumps({"max_feeds": 50, "max_articles_per_feed": 3}),
            now, now,
        ),
    )
    conn.commit()


def _seed_plain_source(conn):
    """Insert a source without fetch_config."""
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO sources (id, name, display_name, source_type, url,
                             fetch_interval_min, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            "src-36kr", "36kr_rss", "36氪", "rss",
            "https://36kr.com/feed", 30, 1, now, now,
        ),
    )
    conn.commit()


# ---------------------------------------------------------------------------
# GET /api/config/sources/{id}/fetch_config
# ---------------------------------------------------------------------------


class TestGetFetchConfig:
    def test_get_with_custom_config(self, client):
        conn = get_db()
        _seed_bestblogs_source(conn)

        resp = client.get("/api/config/sources/src-bestblogs/fetch_config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source_id"] == "src-bestblogs"
        assert data["source_name"] == "bestblogs"
        assert data["has_custom"] is True
        # User overrides
        assert data["fetch_config"]["max_feeds"] == 50
        assert data["fetch_config"]["max_articles_per_feed"] == 3
        # Defaults filled in
        assert data["fetch_config"]["max_articles_total"] == 500
        assert data["fetch_config"]["request_delay"] == 0.5
        assert data["defaults"]["max_feeds"] == 375

    def test_get_without_custom_config(self, client):
        conn = get_db()
        _seed_plain_source(conn)

        resp = client.get("/api/config/sources/src-36kr/fetch_config")
        assert resp.status_code == 200
        data = resp.json()
        assert data["source_id"] == "src-36kr"
        assert data["source_name"] == "36kr_rss"
        assert data["has_custom"] is False
        assert data["fetch_config"] == {}
        assert data["defaults"] == {}

    def test_get_not_found(self, client):
        resp = client.get("/api/config/sources/nonexistent/fetch_config")
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PUT /api/config/sources/{id}/fetch_config
# ---------------------------------------------------------------------------


class TestUpdateFetchConfig:
    def test_update_creates_config(self, client):
        conn = get_db()
        _seed_plain_source(conn)

        new_config = {"max_feeds": 100, "max_articles_total": 200}
        resp = client.put(
            "/api/config/sources/src-36kr/fetch_config",
            json=new_config,
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_custom"] is True
        assert data["fetch_config"]["max_feeds"] == 100
        assert data["fetch_config"]["max_articles_total"] == 200

    def test_update_overrides_existing(self, client):
        conn = get_db()
        _seed_bestblogs_source(conn)

        new_config = {"max_feeds": 200, "request_delay": 1.0}
        resp = client.put(
            "/api/config/sources/src-bestblogs/fetch_config",
            json=new_config,
        )
        assert resp.status_code == 200
        data = resp.json()
        # New values
        assert data["fetch_config"]["max_feeds"] == 200
        assert data["fetch_config"]["request_delay"] == 1.0
        # Defaults still present for unspecified keys
        assert data["fetch_config"]["max_articles_total"] == 500

    def test_update_empty_clears_config(self, client):
        conn = get_db()
        _seed_bestblogs_source(conn)

        resp = client.put(
            "/api/config/sources/src-bestblogs/fetch_config",
            json={},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["has_custom"] is False

    def test_update_not_found(self, client):
        resp = client.put(
            "/api/config/sources/nonexistent/fetch_config",
            json={"max_feeds": 10},
        )
        assert resp.status_code == 404


# ---------------------------------------------------------------------------
# Source model includes fetch_config
# ---------------------------------------------------------------------------


class TestSourceModelFetchConfig:
    def test_source_list_includes_fetch_config(self, client):
        conn = get_db()
        _seed_bestblogs_source(conn)

        resp = client.get("/api/config/sources")
        assert resp.status_code == 200
        sources = resp.json()
        bb = next(s for s in sources if s["name"] == "bestblogs")
        assert bb["fetch_config"] is not None
        assert bb["fetch_config"]["max_feeds"] == 50

    def test_source_get_includes_fetch_config(self, client):
        conn = get_db()
        _seed_bestblogs_source(conn)

        resp = client.get("/api/config/sources/src-bestblogs")
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetch_config"]["max_feeds"] == 50

    def test_source_update_fetch_config(self, client):
        conn = get_db()
        _seed_plain_source(conn)

        resp = client.put(
            "/api/config/sources/src-36kr",
            json={"fetch_config": {"max_feeds": 99}},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["fetch_config"]["max_feeds"] == 99
