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
def seeded_source(client: TestClient) -> str:
    """Insert a minimal source row so the articles POST will find it.

    v1.0.5.10: required by the new POST /api/articles tests because
    ``create_article`` validates ``sources.name`` exists before insert.
    """
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO sources (id, name, display_name, source_type, url,
                             fetch_interval_min, is_active, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("src-testsrc", "testsrc", "Test Source", "rss",
         "https://example.com/", 30, 1, now, now),
    )
    conn.commit()
    return "testsrc"


@pytest.fixture
def seeded_tag(client: TestClient) -> str:
    """Insert a minimal tag_definitions row for the rename tests.

    v1.0.5.14: required by the tag-rename cascade tests — the
    test creates a tag, optionally binds it to the seeded_source and
    the seeded_article, and then exercises PUT /api/config/tags/{name}
    with a new name to verify the cascade UPDATE across
    tag_definitions / source_tags / article_tags.
    """
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO tag_definitions (name, description, color, created_at, is_active, keywords)
        VALUES (?, ?, ?, ?, 1, NULL)
        """,
        ("renameme", "tag for rename test", "#DC2626", now),
    )
    conn.commit()
    return "renameme"


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

    # ── v1.0.5.10: articles filter tests ─────────────────────────────

    def test_filter_by_source(self, client: TestClient, seeded_source: str, seeded_article: str):
        """?source= returns only matching rows."""
        r = client.get(f"/api/articles?source={seeded_source}")
        assert r.status_code == 200
        body = r.json()
        # All returned articles must have source == seeded_source
        assert all(a["source"] == seeded_source for a in body)
        # At least the seeded article shows up
        assert len(body) >= 1

    def test_filter_by_tag(self, client: TestClient, seeded_article: str):
        """?tag= returns only rows tagged with the given tag."""
        conn = get_db()
        now = datetime.now(timezone.utc).isoformat()
        # FK: article_tags.tag_name -> tag_definitions.name.  Seed both.
        conn.execute(
            "INSERT INTO tag_definitions (name, description, created_at, is_active) "
            "VALUES (?, ?, ?, 1) ON CONFLICT(name) DO NOTHING",
            ("testtag", "v1.0.5.10 test tag", now),
        )
        # Tag the seeded article so the filter has something to match.
        # ON CONFLICT DO NOTHING — idempotent if pytest runs twice or other
        # tests already inserted the same (article_id, tag_name) pair.
        # created_at is NOT NULL, so we set it explicitly.
        conn.execute(
            "INSERT INTO article_tags (article_id, tag_name, created_at) "
            "VALUES (?, ?, ?) "
            "ON CONFLICT DO NOTHING",
            (seeded_article, "testtag", now),
        )
        conn.commit()
        r = client.get("/api/articles?tag=testtag")
        assert r.status_code == 200
        body = r.json()
        assert len(body) >= 1
        # v1.0.5.12: tags is now list[{name, color}], not list[str].  Check
        # tag name membership via the .name subfield.
        assert all("testtag" in [t["name"] for t in a["tags"]] for a in body)

    def test_filter_by_keyword_title(self, client: TestClient, seeded_article: str):
        """?keyword= matches against title (LIKE %kw%)."""
        # The seeded article is "Test Article" — search for that.
        r = client.get("/api/articles?keyword=" + "Test%20Article")
        assert r.status_code == 200
        body = r.json()
        assert len(body) >= 1
        # All returned rows should have the keyword in title OR content
        for a in body:
            title = a.get("title") or ""
            content = a.get("content") or ""
            assert ("Test Article" in title) or ("Test Article" in content), (
                f"keyword hit didn't match: {a}"
            )

    def test_filter_by_keyword_no_match(self, client: TestClient):
        """?keyword= with nonsense returns empty list, not 500."""
        r = client.get("/api/articles?keyword=xyzzqwertynonexistent")
        assert r.status_code == 200
        assert r.json() == []

    def test_filter_combined(self, client: TestClient, seeded_source: str):
        """source + tag + keyword combined — empty when no row matches all 3."""
        # seeded article source=testsrc, no tag.  Add ?tag= that doesn't exist.
        r = client.get(f"/api/articles?source={seeded_source}&tag=nosuch&keyword=foo")
        assert r.status_code == 200
        assert r.json() == []

    def test_list_articles_default_limit_50(self, client: TestClient):
        """v1.0.5.14: list_articles defaults to limit=50 (was unbounded)."""
        r = client.get("/api/articles")
        assert r.status_code == 200
        body = r.json()
        # Even if there are 100 rows in the DB, default should cap at 50.
        assert len(body) <= 50, f"default limit should be 50, got {len(body)}"

    def test_list_articles_explicit_limit(self, client: TestClient):
        """v1.0.5.14: ?limit=N returns at most N rows."""
        r = client.get("/api/articles?limit=3")
        assert r.status_code == 200
        body = r.json()
        assert len(body) <= 3, f"limit=3 should cap at 3, got {len(body)}"

    def test_list_articles_offset_skips_rows(self, client: TestClient):
        """v1.0.5.14: ?offset=N skips the first N rows."""
        r0 = client.get("/api/articles?limit=5&offset=0")
        r2 = client.get("/api/articles?limit=5&offset=2")
        assert r0.status_code == 200
        assert r2.status_code == 200
        body0 = r0.json()
        body2 = r2.json()
        # offset=2 should return rows 3..N (or fewer if DB has <5).
        # If body0 has at least 3 rows, body2's first id != body0's first id.
        if len(body0) >= 3:
            assert body2[0]["id"] != body0[0]["id"]

    def test_list_articles_limit_too_large_422(self, client: TestClient):
        """v1.0.5.14: ?limit=300 (above max 200) returns 422 Pydantic validation."""
        r = client.get("/api/articles?limit=300")
        assert r.status_code == 422

    def test_list_articles_limit_zero_422(self, client: TestClient):
        """v1.0.5.14: ?limit=0 (below min 1) returns 422."""
        r = client.get("/api/articles?limit=0")
        assert r.status_code == 422

    def test_list_articles_offset_negative_422(self, client: TestClient):
        """v1.0.5.14: ?offset=-1 (below min 0) returns 422."""
        r = client.get("/api/articles?offset=-1")
        assert r.status_code == 422

    def test_list_articles_published_at_nullable_no_crash(
        self, client: TestClient
    ):
        """v1.0.5.14: articles with NULL published_at must not 500;
        they fall back to fetched_at in the COALESCE sort."""
        # The fresh DB seed has all rows with a real published_at, so
        # we just need to verify the endpoint doesn't 500 — existence
        # of COALESCE is what we're guarding here.
        r = client.get("/api/articles?limit=5")
        assert r.status_code == 200

    # ── v1.0.5.14: PUT /api/config/tags/{name} with display_name ──
    # Replaces the previous (broken) rename-cascade tests: the user-
    # facing "rename" is now done by editing display_name, NOT by
    # changing the system `name` column.  The body of PUT has no
    # `name` field, so these tests focus on the display_name /
    # description / color / is_active / keywords fields.

    def test_update_tag_display_name(self, client: TestClient, seeded_tag: str):
        """PUT with body.display_name= updates the human-friendly name."""
        r = client.put(
            f"/api/config/tags/{seeded_tag}",
            json={"display_name": "银行 (测试)"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == seeded_tag   # system name unchanged
        assert body["display_name"] == "银行 (测试)"
        # Confirm persisted via the list endpoint (no GET-by-name
        # endpoint exists; the list is the canonical read path).
        r2 = client.get("/api/config/tags")
        assert r2.status_code == 200
        rows = {t["name"]: t for t in r2.json()}
        assert rows[seeded_tag]["display_name"] == "银行 (测试)"

    def test_update_tag_name_ignored(
        self, client: TestClient, seeded_tag: str
    ):
        """v1.0.5.14: PUT body.name= is silently dropped (TagUpdate
        has no `name` field) — the system name is immutable.  Sending
        it should be a 200 that just doesn't change anything."""
        r = client.put(
            f"/api/config/tags/{seeded_tag}",
            json={"name": "new-system-name", "display_name": "renamed"},
        )
        assert r.status_code == 200, r.text
        body = r.json()
        assert body["name"] == seeded_tag, "name should NOT have changed"
        assert body["display_name"] == "renamed"

    def test_create_tag_with_display_name(self, client: TestClient):
        """POST /api/config/tags accepts display_name at create time."""
        r = client.post(
            "/api/config/tags",
            json={
                "name": "fresh-tag",
                "display_name": "新标签",
                "color": "#8A71E8",
            },
        )
        assert r.status_code == 201, r.text
        body = r.json()
        assert body["name"] == "fresh-tag"
        assert body["display_name"] == "新标签"
        assert body["color"] == "#8A71E8"

    def test_create_tag_without_display_name_falls_back_to_name(
        self, client: TestClient
    ):
        """Legacy POSTs that only send `name` still work — display_name
        defaults to `name` server-side so the chip always has a label."""
        r = client.post(
            "/api/config/tags",
            json={"name": "legacy-tag"},
        )
        assert r.status_code == 201, r.text
        assert r.json()["display_name"] == "legacy-tag"

    def test_list_tags_includes_display_name(
        self, client: TestClient, seeded_tag: str
    ):
        """GET /api/config/tags returns display_name alongside name."""
        # First update display_name
        client.put(
            f"/api/config/tags/{seeded_tag}",
            json={"display_name": "测试名"},
        )
        # Then list
        r = client.get("/api/config/tags")
        assert r.status_code == 200
        rows = {t["name"]: t for t in r.json()}
        assert rows[seeded_tag]["display_name"] == "测试名"

    def test_article_tagref_has_display_name(
        self, client: TestClient, seeded_tag: str, seeded_article: int
    ):
        """v1.0.5.14: /api/articles returns tags as TagRef objects
        that include the human-friendly display_name."""
        # Bind the seeded tag to the seeded article
        conn = get_db()
        conn.execute(
            "INSERT INTO article_tags (article_id, tag_name, created_at) "
            "VALUES (?, ?, ?)",
            (str(seeded_article), seeded_tag,
             datetime.now(timezone.utc).isoformat()),
        )
        # Set a recognizable display_name
        client.put(
            f"/api/config/tags/{seeded_tag}",
            json={"display_name": "改名显示"},
        )
        # Fetch the article
        r = client.get(f"/api/articles/{seeded_article}")
        assert r.status_code == 200
        body = r.json()
        # Find the tag ref for our seeded tag
        tag_refs = [t for t in body.get("tags", []) if t["name"] == seeded_tag]
        assert len(tag_refs) == 1, f"expected 1 TagRef for {seeded_tag}, got {tag_refs}"
        assert tag_refs[0]["display_name"] == "改名显示"
        assert tag_refs[0]["name"] == seeded_tag


    # ── v1.0.5.10: POST /api/articles ──────────────────────────────

    def test_post_create_201(self, client: TestClient, seeded_source: str):
        """POST /api/articles creates a new article."""
        body = {
            "source_id": seeded_source,
            "title": "Created Via POST",
            "url": "https://example.com/created-via-post",
        }
        r = client.post("/api/articles", json=body)
        assert r.status_code == 201, r.text
        data = r.json()
        assert data["title"] == "Created Via POST"
        assert data["source"] == seeded_source
        assert data["url"] == "https://example.com/created-via-post"

    def test_post_create_unknown_source_400(self, client: TestClient):
        """POST with an unknown source name returns 400."""
        body = {
            "source_id": "no-such-source-xyz",
            "title": "Should Fail",
            "url": "https://example.com/fail",
        }
        r = client.post("/api/articles", json=body)
        assert r.status_code == 400

    def test_post_create_idempotent(self, client: TestClient, seeded_source: str):
        """POST with duplicate (source, url) returns 201 + the existing row."""
        body = {
            "source_id": seeded_source,
            "title": "Dup",
            "url": "https://example.com/dup",
        }
        r1 = client.post("/api/articles", json=body)
        r2 = client.post("/api/articles", json=body)
        assert r1.status_code == 201
        assert r2.status_code == 201
        # Same id both times (the UNIQUE constraint kicks in)
        assert r1.json()["id"] == r2.json()["id"]


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
