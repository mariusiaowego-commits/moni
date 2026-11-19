"""tests/test_basket.py — pytest coverage for basket (菜篮子) functionality."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone

import pytest

# Ensure moni is importable from the repo root
sys.path.insert(0, os.path.expanduser("~/dev/moni/wt/t1-db"))

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


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _seed_basket_item(
    source: str = "test_source",
    url: str = "https://example.com/article1",
    title: str = "Test Article",
    dropped_reason: str = "blacklist",
    dropped_by: str = "test_keyword",
    raw_content: str = "Test content",
) -> str:
    """Insert a basket item and return its id."""
    import hashlib
    from datetime import datetime, timezone

    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    raw_id = f"{source}{url}{title}"
    item_id = hashlib.sha256(raw_id.encode()).hexdigest()[:16]

    conn.execute(
        """INSERT OR IGNORE INTO basket
           (id, title, url, source, publisher, published_at, fetched_at,
            raw_content, dropped_reason, dropped_by, created_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (item_id, title, url, source, "test_publisher", now, now,
         raw_content, dropped_reason, dropped_by, now),
    )
    conn.commit()
    return item_id


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestBasketTableCreation:
    """Test basket table exists after migration."""

    def test_basket_table_exists(self):
        conn = get_db()
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='basket'"
        ).fetchone()
        assert row is not None

    def test_basket_table_schema(self):
        conn = get_db()
        cursor = conn.execute("PRAGMA table_info(basket)")
        columns = {row[1] for row in cursor.fetchall()}
        expected = {
            "id", "title", "url", "source", "publisher", "published_at",
            "fetched_at", "raw_content", "dropped_reason", "dropped_by",
            "rescued_at", "created_at",
        }
        assert expected.issubset(columns)


class TestBasketAPI:
    """Test basket API endpoints."""

    def test_list_basket_empty(self):
        client = _get_app()
        resp = client.get("/api/basket")
        assert resp.status_code == 200
        data = resp.json()
        assert data["items"] == []
        assert data["total"] == 0

    def test_list_basket_with_items(self):
        _seed_basket_item()
        client = _get_app()
        resp = client.get("/api/basket")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["total"] == 1
        assert data["items"][0]["source"] == "test_source"

    def test_list_basket_filter_by_source(self):
        _seed_basket_item(source="source_a")
        _seed_basket_item(
            source="source_b",
            url="https://example.com/article2",
            title="Test Article 2",
        )
        client = _get_app()
        resp = client.get("/api/basket?source=source_a")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["source"] == "source_a"

    def test_list_basket_filter_by_reason(self):
        _seed_basket_item(dropped_reason="blacklist")
        _seed_basket_item(
            dropped_reason="tag_filter",
            url="https://example.com/article2",
            title="Test Article 2",
        )
        client = _get_app()
        resp = client.get("/api/basket?reason=blacklist")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["dropped_reason"] == "blacklist"

    def test_get_basket_item(self):
        item_id = _seed_basket_item()
        client = _get_app()
        resp = client.get(f"/api/basket/{item_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["id"] == item_id
        assert data["title"] == "Test Article"

    def test_get_basket_item_not_found(self):
        client = _get_app()
        resp = client.get("/api/basket/nonexistent")
        assert resp.status_code == 404

    def test_delete_basket_item(self):
        item_id = _seed_basket_item()
        client = _get_app()
        resp = client.delete(f"/api/basket/{item_id}")
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

        # Verify deleted
        resp = client.get(f"/api/basket/{item_id}")
        assert resp.status_code == 404

    def test_delete_basket_item_not_found(self):
        client = _get_app()
        resp = client.delete("/api/basket/nonexistent")
        assert resp.status_code == 404

    def test_rescue_basket_item(self):
        item_id = _seed_basket_item()
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO tag_definitions (name, display_name, is_active, created_at) VALUES (?, ?, ?, ?)",
            ("ai-fintech", "AI×金融科技", 1, now),
        )
        conn.commit()

        client = _get_app()
        resp = client.post(
            f"/api/basket/{item_id}/rescue",
            json={"tags": ["ai-fintech"]},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["ok"] is True
        assert data["article_id"] is not None

        # Verify rescued_at is set
        resp = client.get(f"/api/basket/{item_id}")
        assert resp.status_code == 200
        assert resp.json()["rescued_at"] is not None

    def test_rescue_basket_item_already_rescued(self):
        item_id = _seed_basket_item()
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO tag_definitions (name, display_name, is_active, created_at) VALUES (?, ?, ?, ?)",
            ("ai-fintech", "AI×金融科技", 1, now),
        )
        conn.commit()

        client = _get_app()

        # First rescue
        resp = client.post(
            f"/api/basket/{item_id}/rescue",
            json={"tags": ["ai-fintech"]},
        )
        assert resp.json()["ok"] is True

        # Second rescue should return already rescued
        resp = client.post(
            f"/api/basket/{item_id}/rescue",
            json={"tags": ["ai-fintech"]},
        )
        assert resp.json()["ok"] is False
        assert "已被 rescue" in resp.json()["message"]

    def test_rescue_basket_item_not_found(self):
        client = _get_app()
        resp = client.post(
            "/api/basket/nonexistent/rescue",
            json={"tags": ["ai-fintech"]},
        )
        assert resp.status_code == 404

    def test_rescue_rejects_body_that_cleans_to_empty(self):
        """A 451-char footer-only body must not become an empty article."""
        item_id = _seed_basket_item(raw_content="本文来源：某某公众号\n" * 41)
        client = _get_app()
        resp = client.post(f"/api/basket/{item_id}/rescue", json={"tags": []})
        assert resp.status_code == 400
        assert resp.json()["code"] == "bad_request"

        # Nothing is written: the item stays rescuable after its body is fixed.
        conn = get_db()
        assert conn.execute(
            "SELECT rescued_at FROM basket WHERE id = ?", (item_id,)
        ).fetchone()["rescued_at"] is None
        assert conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 0

    def test_rescue_allows_title_only_item(self):
        """Compliance title-only items (no body) stay rescuable."""
        item_id = _seed_basket_item(raw_content="")
        client = _get_app()
        resp = client.post(f"/api/basket/{item_id}/rescue", json={"tags": []})
        assert resp.status_code == 200
        assert resp.json()["ok"] is True

    def test_rescue_creates_article(self):
        item_id = _seed_basket_item()
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO tag_definitions (name, display_name, is_active, created_at) VALUES (?, ?, ?, ?)",
            ("ai-fintech", "AI×金融科技", 1, now),
        )
        conn.commit()

        client = _get_app()

        # Rescue
        resp = client.post(
            f"/api/basket/{item_id}/rescue",
            json={"tags": ["ai-fintech"]},
        )
        assert resp.json()["ok"] is True
        article_id = resp.json()["article_id"]

        # Verify article exists
        row = conn.execute(
            "SELECT id FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        assert row is not None

    def test_rescue_with_tags(self):
        item_id = _seed_basket_item()
        client = _get_app()

        # Create a tag first
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO tag_definitions (name, display_name, is_active, created_at) VALUES (?, ?, ?, ?)",
            ("test_tag", "Test Tag", 1, now),
        )
        conn.commit()

        # Rescue with tag
        resp = client.post(
            f"/api/basket/{item_id}/rescue",
            json={"tags": ["test_tag"]},
        )
        assert resp.json()["ok"] is True
        article_id = resp.json()["article_id"]

        # Verify article_tags
        row = conn.execute(
            "SELECT tag_name FROM article_tags WHERE article_id = ?",
            (article_id,),
        ).fetchone()
        assert row is not None
        assert row["tag_name"] == "test_tag"


class TestBasketPagination:
    """Test basket pagination."""

    def test_pagination(self):
        # Create 5 items
        for i in range(5):
            _seed_basket_item(
                url=f"https://example.com/article{i}",
                title=f"Article {i}",
            )

        client = _get_app()

        # Page 1
        resp = client.get("/api/basket?page=1&page_size=2")
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5
        assert data["page"] == 1
        assert data["page_size"] == 2

        # Page 2
        resp = client.get("/api/basket?page=2&page_size=2")
        data = resp.json()
        assert len(data["items"]) == 2
        assert data["total"] == 5

        # Page 3
        resp = client.get("/api/basket?page=3&page_size=2")
        data = resp.json()
        assert len(data["items"]) == 1
        assert data["total"] == 5


class TestBasketFilterIntegration:
    """Test that filters write to basket on drop."""

    def test_blacklist_drop_writes_basket(self):
        """Test that blacklist filter drops write to basket."""
        from moni.fetch.filters import apply_keyword_filters

        # Seed a source and filter
        conn = get_db()
        conn.execute(
            "INSERT OR IGNORE INTO sources (id, name, source_type, url, fetch_interval_min, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("test-src", "test_source", "rss", "https://example.com", 30, 1,
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        conn.execute(
            "INSERT OR IGNORE INTO source_keyword_filters (source_id, mode, match_field, keywords, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            ("test-src", "blacklist", "title+content", '["bad_keyword"]', 1,
             datetime.now(timezone.utc).isoformat(), datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()

        # Create an article that will be dropped
        articles = [
            {
                "title": "Article with bad_keyword",
                "url": "https://example.com/1",
                "source": "test_source",
                "content": "This contains bad_keyword",
            }
        ]

        # Apply filters
        kept, stats, hits = apply_keyword_filters("test_source", articles)

        # Verify article was dropped
        assert stats["dropped_blacklist"] == 1
        assert len(kept) == 0

        # Verify basket has the item
        row = conn.execute("SELECT COUNT(*) FROM basket").fetchone()
        assert row[0] == 1

    def test_tag_filter_drop_writes_basket(self):
        """Test that tag filter drops write to basket."""
        from moni.fetch.tag_filter import apply_tag_keyword_filter

        # Seed a source and tag with keywords
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT OR IGNORE INTO sources (id, name, source_type, url, fetch_interval_min, is_active, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ("test-src", "test_source", "rss", "https://example.com", 30, 1, now, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO tag_definitions (name, display_name, keywords, is_active, created_at) VALUES (?, ?, ?, ?, ?)",
            ("ai-fintech", "AI×金融科技", '["AI", "金融科技"]', 1, now),
        )
        conn.execute(
            "INSERT OR IGNORE INTO source_tags (source_id, tag_name, created_at) VALUES (?, ?, ?)",
            ("test-src", "ai-fintech", now),
        )
        conn.commit()

        # Create an article that will be dropped (no tag keywords match)
        articles = [
            {
                "title": "Unrelated article",
                "url": "https://example.com/1",
                "source": "test_source",
                "content": "This is about cooking",
            }
        ]

        # Apply tag filter
        kept, stats, hits = apply_tag_keyword_filter("test_source", articles)

        # Verify article was dropped
        assert stats["dropped_tag_filter"] == 1
        assert len(kept) == 0

        # Verify basket has the item
        row = conn.execute("SELECT COUNT(*) FROM basket").fetchone()
        assert row[0] == 1
