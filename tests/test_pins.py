"""Unit tests for v1.0.8 article pin feature.

Covers:
* POST /api/articles/{id}/pin?on=true|false (toggle + idempotent)
* DELETE /api/articles/{id}/pin (explicit unpin)
* GET /api/articles/pinned (filters: sources/tags/sort/page)
* POST /api/articles/pins/bulk-unpin
* Listing endpoints surface ``is_pinned`` / ``pinned_at``
* FK CASCADE: deleting an article drops its pin
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest
from fastapi.testclient import TestClient


# ── fixtures (mirrors test_api.py pattern) ────────────────────────────────


@pytest.fixture(autouse=True)
def fresh_db(tmp_path, monkeypatch):
    """Reset the database to a temp file before every test."""
    from moni.db.connection import set_db_path, close_db
    from moni.db.migrate import migrate
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
    from moni.web.server import create_app
    return TestClient(create_app())


@pytest.fixture
def seeded_source(client: TestClient) -> str:
    """Insert a minimal source so article inserts can pass FK checks.

    Uses a unique source name per test to avoid UNIQUE collisions
    with other test files (test_basket, test_api also seed 'mpaypass').
    """
    from moni.db.connection import get_db
    import uuid
    suffix = uuid.uuid4().hex[:8]
    name = f"mp_{suffix}"
    src_id = f"src-{name}"
    now = datetime.now(timezone.utc).isoformat()
    get_db().execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (src_id, name, "支付", "rss",
         f"https://example.com/{name}", 30, 1, now, now),
    )
    get_db().execute(
        "INSERT INTO sources (id, name, display_name, source_type, url, "
        "fetch_interval_min, is_active, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (f"src-{name}-b", f"{name}_b", "36氪", "rss",
         f"https://example.com/{name}_b", 30, 1, now, now),
    )
    get_db().commit()
    return name


def _seed_article(source: str | None = None, title: str = "test article",
                  article_id: str = "a1") -> str:
    """Insert a test article.  If ``source`` is None, callers must
    seed the source first (use ``seeded_source`` fixture)."""
    from moni.db.connection import get_db
    now = datetime.now(timezone.utc).isoformat()
    get_db().execute(
        "INSERT INTO articles (id, title, url, source, fetched_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (article_id, title, f"https://example.com/{article_id}", source, now),
    )
    get_db().commit()
    return article_id


# ── POST /api/articles/{id}/pin ───────────────────────────────────────────


class TestTogglePin:
    def test_pin_new_article_returns_pinned(self, client, seeded_source):
        aid = _seed_article(seeded_source)
        r = client.post(f"/api/articles/{aid}/pin?on=true")
        assert r.status_code == 200
        body = r.json()
        assert body["article_id"] == aid
        assert body["is_pinned"] is True
        assert body["pinned_at"] is not None

    def test_unpin_pinned_article_returns_unpinned(self, client, seeded_source):
        aid = _seed_article(seeded_source)
        client.post(f"/api/articles/{aid}/pin?on=true")
        r = client.post(f"/api/articles/{aid}/pin?on=false")
        assert r.status_code == 200
        body = r.json()
        assert body["is_pinned"] is False
        assert body["pinned_at"] is None

    def test_repin_refreshes_pinned_at(self, client, seeded_source):
        """Repinning an already-pinned article should update pinned_at,
        not fail.  Useful for 'bump to top' UX."""
        aid = _seed_article(seeded_source)
        r1 = client.post(f"/api/articles/{aid}/pin?on=true")
        first_at = r1.json()["pinned_at"]
        # Even at sub-millisecond resolution, the second pin must
        # produce a pinned_at >= the first.  Sleep 10ms to be safe.
        import time
        time.sleep(0.01)
        r2 = client.post(f"/api/articles/{aid}/pin?on=true")
        second_at = r2.json()["pinned_at"]
        assert second_at >= first_at, f"re-pin should refresh: {first_at} → {second_at}"

    def test_pin_nonexistent_article_404(self, client, seeded_source):
        r = client.post("/api/articles/no-such-article/pin?on=true")
        assert r.status_code == 404

    def test_default_on_true_when_query_param_omitted(self, client, seeded_source):
        """Default to on=true when ?on= is not provided — convenient
        for the most common case from the SPA."""
        aid = _seed_article(seeded_source)
        r = client.post(f"/api/articles/{aid}/pin")
        assert r.status_code == 200
        assert r.json()["is_pinned"] is True


# ── DELETE /api/articles/{id}/pin ─────────────────────────────────────────


class TestDeletePin:
    def test_delete_existing_pin(self, client, seeded_source):
        aid = _seed_article(seeded_source)
        client.post(f"/api/articles/{aid}/pin?on=true")
        r = client.delete(f"/api/articles/{aid}/pin")
        assert r.status_code == 200
        assert r.json()["is_pinned"] is False

    def test_delete_nonexistent_pin_idempotent(self, client, seeded_source):
        """Unpinning an article that was never pinned is a no-op."""
        aid = _seed_article(seeded_source)
        r = client.delete(f"/api/articles/{aid}/pin")
        assert r.status_code == 200
        assert r.json()["is_pinned"] is False

    def test_delete_pin_does_not_delete_article(self, client, seeded_source):
        aid = _seed_article(seeded_source)
        client.post(f"/api/articles/{aid}/pin?on=true")
        client.delete(f"/api/articles/{aid}/pin")
        # Article still exists.
        r = client.get(f"/api/articles/{aid}")
        assert r.status_code == 200


# ── GET /api/articles/pinned ──────────────────────────────────────────────


class TestListPinned:
    def test_empty_when_nothing_pinned(self, client, seeded_source):
        r = client.get("/api/articles/pinned")
        assert r.status_code == 200
        body = r.json()
        assert body["items"] == []
        assert body["total"] == 0

    def test_lists_all_pinned_by_default(self, client, seeded_source):
        for i in range(3):
            _seed_article(seeded_source, article_id=f"a{i+1}")
            client.post(f"/api/articles/a{i+1}/pin?on=true")
        r = client.get("/api/articles/pinned")
        body = r.json()
        assert body["total"] == 3
        assert len(body["items"]) == 3
        for item in body["items"]:
            assert item["is_pinned"] is True
            assert item["pinned_at"] is not None

    def test_filter_by_sources_multi_select_union(self, client, seeded_source):
        # 2 from primary source, 1 from secondary
        primary = seeded_source
        secondary = f"{seeded_source}_b"
        _seed_article(primary, article_id="a1")
        _seed_article(primary, article_id="a2")
        _seed_article(secondary, article_id="b1")
        for aid in ["a1", "a2", "b1"]:
            client.post(f"/api/articles/{aid}/pin?on=true")
        # Filter: only primary → 2 results
        r = client.get(f"/api/articles/pinned?sources={primary}")
        body = r.json()
        assert body["total"] == 2
        assert {item["id"] for item in body["items"]} == {"a1", "a2"}
        # Filter: both sources (union) → 3 results
        r = client.get(f"/api/articles/pinned?sources={primary},{secondary}")
        body = r.json()
        assert body["total"] == 3

    def test_filter_by_tags(self, client, seeded_source):
        # Setup: 2 articles, one with 'banking' tag, one with 'ai' tag
        _seed_article(seeded_source, article_id="a1")
        _seed_article(seeded_source, article_id="a2")
        for aid in ["a1", "a2"]:
            client.post(f"/api/articles/{aid}/pin?on=true")
        from moni.db.connection import get_db
        now = datetime.now(timezone.utc).isoformat()
        get_db().execute(
            "INSERT INTO tag_definitions (name, display_name, color, created_at, is_active) "
            "VALUES (?, ?, ?, ?, 1)", ("banking", "银行", "#000", now),
        )
        get_db().execute(
            "INSERT INTO article_tags (article_id, tag_name, created_at) VALUES (?, ?, ?)",
            ("a1", "banking", now),
        )
        get_db().commit()
        # Filter: only banking → 1 result
        r = client.get("/api/articles/pinned?tags=banking")
        body = r.json()
        assert body["total"] == 1
        assert body["items"][0]["id"] == "a1"

    def test_sort_by_pinned_at_desc_default(self, client, seeded_source):
        _seed_article(seeded_source, article_id="a1")
        _seed_article(seeded_source, article_id="a2")
        client.post("/api/articles/a1/pin?on=true")
        import time
        time.sleep(0.01)
        client.post("/api/articles/a2/pin?on=true")
        r = client.get("/api/articles/pinned")
        ids = [item["id"] for item in r.json()["items"]]
        # a2 pinned later → first
        assert ids[0] == "a2"
        assert ids[1] == "a1"

    def test_sort_by_pinned_at_asc(self, client, seeded_source):
        _seed_article(seeded_source, article_id="a1")
        _seed_article(seeded_source, article_id="a2")
        client.post("/api/articles/a1/pin?on=true")
        import time
        time.sleep(0.01)
        client.post("/api/articles/a2/pin?on=true")
        r = client.get("/api/articles/pinned?sort=pinned_at_asc")
        ids = [item["id"] for item in r.json()["items"]]
        assert ids[0] == "a1"
        assert ids[1] == "a2"

    def test_pagination(self, client, seeded_source):
        for i in range(5):
            _seed_article(seeded_source, article_id=f"a{i+1}")
            client.post(f"/api/articles/a{i+1}/pin?on=true")
        r = client.get("/api/articles/pinned?page=1&page_size=2")
        body = r.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
        assert body["page"] == 1
        assert body["page_size"] == 2
        r2 = client.get("/api/articles/pinned?page=3&page_size=2")
        assert len(r2.json()["items"]) == 1  # last page has 1

    def test_sort_by_title(self, client, seeded_source):
        _seed_article(seeded_source, title="Apple", article_id="a1")
        _seed_article(seeded_source, title="Banana", article_id="a2")
        _seed_article(seeded_source, title="Cherry", article_id="a3")
        for aid in ["a1", "a2", "a3"]:
            client.post(f"/api/articles/{aid}/pin?on=true")
        r = client.get("/api/articles/pinned?sort=title_asc")
        titles = [item["title"] for item in r.json()["items"]]
        assert titles == ["Apple", "Banana", "Cherry"]


# ── POST /api/articles/pins/bulk-unpin ────────────────────────────────────


class TestBulkUnpin:
    def test_bulk_unpin_multiple(self, client, seeded_source):
        ids = []
        for i in range(3):
            aid = _seed_article(seeded_source, article_id=f"a{i+1}")
            client.post(f"/api/articles/{aid}/pin?on=true")
            ids.append(aid)
        r = client.post("/api/articles/pins/bulk-unpin",
                        json={"article_ids": ids})
        assert r.status_code == 200
        body = r.json()
        assert sorted(body["unpinned"]) == sorted(ids)
        assert body["not_found"] == []

    def test_bulk_unpin_partial_not_found(self, client, seeded_source):
        aid = _seed_article(seeded_source, article_id="a1")
        client.post("/api/articles/a1/pin?on=true")
        r = client.post("/api/articles/pins/bulk-unpin",
                        json={"article_ids": ["a1", "never-pinned"]})
        body = r.json()
        assert body["unpinned"] == ["a1"]
        assert body["not_found"] == ["never-pinned"]

    def test_bulk_unpin_empty_list(self, client, seeded_source):
        r = client.post("/api/articles/pins/bulk-unpin",
                        json={"article_ids": []})
        assert r.status_code == 200
        assert r.json() == {"unpinned": [], "not_found": []}

    def test_bulk_unpin_then_list_pinned_is_empty(self, client, seeded_source):
        for i in range(2):
            aid = _seed_article(seeded_source, article_id=f"a{i+1}")
            client.post(f"/api/articles/{aid}/pin?on=true")
        r = client.post("/api/articles/pins/bulk-unpin",
                        json={"article_ids": ["a1", "a2"]})
        assert r.status_code == 200
        # Now /pinned should be empty
        r = client.get("/api/articles/pinned")
        assert r.json()["total"] == 0


# ── Listing endpoints surface is_pinned / pinned_at ──────────────────────


class TestListingSurfacesPinStatus:
    def test_get_article_returns_pin_status(self, client, seeded_source):
        aid = _seed_article(seeded_source)
        # Not pinned yet
        r = client.get(f"/api/articles/{aid}")
        assert r.status_code == 200
        body = r.json()
        assert body["is_pinned"] is False
        assert body["pinned_at"] is None
        # Pin it
        client.post(f"/api/articles/{aid}/pin?on=true")
        r = client.get(f"/api/articles/{aid}")
        body = r.json()
        assert body["is_pinned"] is True
        assert body["pinned_at"] is not None

    def test_list_articles_returns_pin_status(self, client, seeded_source):
        _seed_article(seeded_source, article_id="a1")
        _seed_article(seeded_source, article_id="a2")
        client.post("/api/articles/a1/pin?on=true")
        r = client.get("/api/articles")
        assert r.status_code == 200
        items = {item["id"]: item for item in r.json()}
        assert items["a1"]["is_pinned"] is True
        assert items["a1"]["pinned_at"] is not None
        assert items["a2"]["is_pinned"] is False
        assert items["a2"]["pinned_at"] is None


# ── FK CASCADE ────────────────────────────────────────────────────────────


class TestCascadeOnArticleDelete:
    def test_deleting_article_removes_pin(self, client, seeded_source):
        from moni.db.connection import get_db
        aid = _seed_article(seeded_source)
        client.post(f"/api/articles/{aid}/pin?on=true")
        # Verify pin exists
        r = client.get("/api/articles/pinned")
        assert r.json()["total"] == 1
        # Delete the article via raw SQL (no DELETE endpoint exists)
        get_db().execute("DELETE FROM articles WHERE id = ?", (aid,))
        get_db().commit()
        # Pin should be gone (CASCADE)
        r = client.get("/api/articles/pinned")
        assert r.json()["total"] == 0
        # And the article_pins row itself
        count = get_db().execute(
            "SELECT COUNT(*) AS n FROM article_pins WHERE article_id = ?", (aid,)
        ).fetchone()["n"]
        assert count == 0


# ── /pinned route precedence (regression: shouldn't be matched as article_id) ─


class TestRoutePrecedence:
    def test_pinned_does_not_match_as_article_id(self, client, seeded_source):
        """GET /api/articles/pinned must hit the pins router, not be
        interpreted as article_id='pinned' (which would 404)."""
        r = client.get("/api/articles/pinned")
        assert r.status_code == 200
        # If it had been matched as article_id, the response would be
        # 404 with a different body shape.
        assert "items" in r.json()
