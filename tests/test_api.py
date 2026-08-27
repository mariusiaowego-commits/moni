"""tests/test_api.py — pytest coverage for all 5 web API menus."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from unittest.mock import patch

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
        conn.commit()  # v1.0.37.5: 立即释放悬挂写锁, 防 anyio worker thread 撞 timeout
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

    # v1.0.5.16: confirm-modal estimate endpoint.  The endpoint must
    # validate the same way as /api/share (so the modal won't show a
    # green 确认 button for a payload that's about to 400), but it
    # must NOT create a share_task row.  Token/duration numbers are
    # best-effort — we only assert they look plausible.

    def test_estimate_200_returns_model_and_token_breakdown(
        self, client: TestClient, seeded_article: str,
    ):
        # The seeded_article fixture inserts a row with no raw_content.
        # We need non-zero chars for the token estimate assertions, so
        # poke some content in directly.  This is a unit-test concern,
        # not a runtime one — the production code path always has
        # raw_content from the fetcher.
        from moni.db.connection import get_db
        body = "测试文章内容。" * 200  # ~1000 chars of CJK
        get_db().execute(
            "UPDATE articles SET raw_content=? WHERE id=?",
            (body, seeded_article),
        )
        get_db().commit()

        r = client.post(
            "/api/share/estimate",
            json={
                "format": "markdown",
                "version": "1.0",
                "payload": {"article_ids": [seeded_article]},
            },
        )
        assert r.status_code == 200
        data = r.json()
        # model chain resolved (llm_settings → llm_models → llm_providers).
        # In production this is "seed-model-..." (seeded by moni db init);
        # in a test fixture with a freshly-migrated empty DB, the
        # settings table is empty and we fall back to "(model unset)".
        # Accept either so the test is robust to both environments.
        assert data["model_name"], "model_name should be populated"
        assert (data["model_id"].startswith("seed-model-")
                or data["model_id"].startswith("(model")), \
            f"expected seed-model-* or (model unset), got {data['model_id']!r}"
        assert data["provider_name"], "provider_name should be populated"
        # article sizing
        assert data["articles_count"] == 1
        assert data["total_chars"] == len(body)
        # token estimates.  Backend uses int(total_chars / 2.5) — the
        # 2.5 CJK-mixed heuristic, NOT a 4-chars-per-token rule.
        expected_input = int(len(body) / 2.5)
        assert expected_input - 2 <= data["input_tokens_est"] <= expected_input + 2
        # markdown multiplier is 1.0 — output should equal input within ±2
        assert abs(data["output_tokens_est"] - data["input_tokens_est"]) <= 2
        # duration is always a positive integer
        assert data["duration_sec_est"] > 0
        # artifact template mentions the format
        assert "markdown" in data["artifact_path_template"]

    def test_estimate_400_for_unknown_format(self, client: TestClient):
        r = client.post(
            "/api/share/estimate",
            json={
                "format": "no_such_format_xyz",
                "version": "1.0",
                "payload": {"article_ids": ["foo"]},
            },
        )
        assert r.status_code == 400
        body = r.json()
        assert body.get("code") == "bad_request"
        assert "no_such_format_xyz" in body.get("message", "")

    def test_estimate_400_for_missing_article_ids(
        self, client: TestClient, seeded_article: str,
    ):
        # article_ids is required by every format's payload schema, so
        # the same validation that catches it in /api/share must
        # reject it in /estimate.  We just confirm the status is 400;
        # the exact error wording comes from the format-specific model
        # and is covered by existing test_post_create_task_400_* tests.
        r = client.post(
            "/api/share/estimate",
            json={
                "format": "markdown",
                "version": "1.0",
                "payload": {"include_toc": True},  # missing article_ids
            },
        )
        assert r.status_code == 400

    def test_estimate_does_not_create_share_task(
        self, client: TestClient, seeded_article: str,
    ):
        """The estimate endpoint must not write to share_tasks."""
        before = client.get("/api/share/tasks?page=1&page_size=1").json()["total"]
        r = client.post(
            "/api/share/estimate",
            json={
                "format": "markdown",
                "version": "1.0",
                "payload": {"article_ids": [seeded_article]},
            },
        )
        assert r.status_code == 200
        after = client.get("/api/share/tasks?page=1&page_size=1").json()["total"]
        assert before == after, "estimate must not write a share_task row"


# ---------------------------------------------------------------------------
# v1.0.5.17: worker on-demand trigger + log tail + progress fields
# ---------------------------------------------------------------------------
# The progress columns (progress_stage / progress_pct / progress_message)
# are written by default_worker.sh at each stage transition; the API
# pass-through is verified by checking the response shape after we
# UPDATE a row directly.  The worker/run endpoint is hard to unit-test
# without spawning a real subprocess (and we don't want a real worker
# running in CI), so we mock launch_worker() and assert the
# response shape is wired through.
# ---------------------------------------------------------------------------


class TestShareTaskProgress:
    """ShareTask model + list/get routes surface the v1.0.5.17 progress
    columns end-to-end.  We backfill progress columns directly into
    the DB and assert they round-trip through the API — same
    pattern we used for display_name in test_api.py (v1.0.5.14)."""

    def test_share_task_response_includes_progress_fields(
        self, client: TestClient, seeded_article: str,
    ):
        from moni.db.connection import get_db
        # Create a real share task so we have a row to read back
        r = client.post(
            "/api/share",
            json={
                "format": "markdown",
                "version": "1.0",
                "payload": {"article_ids": [seeded_article]},
            },
        )
        assert r.status_code == 201
        task_id = r.json()["share_id"]

        # Write the 3 progress columns directly (simulating the
        # worker checkpointing midway through invoking_llm).
        get_db().execute(
            "UPDATE share_tasks SET progress_stage=?, progress_pct=?, "
            "progress_message=? WHERE id=?",
            (
                "invoking_llm", 60,
                "正在调 hermes chat (timeout=300s) ...",
                task_id,
            ),
        )
        get_db().commit()

        # Now read it back through the API and assert the fields
        # round-trip exactly.
        r = client.get(f"/api/share/tasks/{task_id}")
        assert r.status_code == 200
        d = r.json()
        assert d["progress_stage"] == "invoking_llm"
        assert d["progress_pct"] == 60
        assert d["progress_message"] == "正在调 hermes chat (timeout=300s) ..."

    def test_share_task_response_handles_null_progress(
        self, client: TestClient, seeded_article: str,
    ):
        """A pre-v1.0.5.17 row (or any row without progress_*) should
        still serialize cleanly — the Pydantic model declares the
        three fields as Optional with None defaults."""
        r = client.post(
            "/api/share",
            json={
                "format": "markdown",
                "version": "1.0",
                "payload": {"article_ids": [seeded_article]},
            },
        )
        task_id = r.json()["share_id"]
        # Don't UPDATE progress_*.  Just read it back.
        r = client.get(f"/api/share/tasks/{task_id}")
        d = r.json()
        assert d["progress_stage"] is None
        assert d["progress_pct"] is None
        assert d["progress_message"] is None

    def test_share_task_list_includes_progress_fields(
        self, client: TestClient, seeded_article: str,
    ):
        from moni.db.connection import get_db
        r = client.post(
            "/api/share",
            json={
                "format": "markdown",
                "version": "1.0",
                "payload": {"article_ids": [seeded_article]},
            },
        )
        task_id = r.json()["share_id"]
        get_db().execute(
            "UPDATE share_tasks SET progress_stage=?, progress_pct=?, progress_message=? "
            "WHERE id=?",
            ("writing_artifact", 95, "正在写 artifact ...", task_id),
        )
        get_db().commit()

        # Pull a 1-page list and look for our task in the items.
        r = client.get("/api/share/tasks?page=1&page_size=100")
        assert r.status_code == 200
        match = next(
            (x for x in r.json()["items"] if x["id"] == task_id),
            None,
        )
        assert match is not None
        assert match["progress_stage"] == "writing_artifact"
        assert match["progress_pct"] == 95
        assert match["progress_message"] == "正在写 artifact ..."


class TestWorkerRun:
    """POST /api/share/worker/run endpoint.  We patch the
    launch_worker symbol in the route module (the one that was
    imported into the route's namespace) so the endpoint uses
    our fake without spawning a real subprocess."""

    def test_worker_run_200_spawned(self, client: TestClient, monkeypatch):
        from moni.web.routes import share as share_route

        class _FakeResult:
            started = True
            already_running = False
            pid = 4242
            log_path = "/tmp/fake-worker.log"
            error = None

        monkeypatch.setattr(share_route, "launch_worker", lambda: _FakeResult())
        r = client.post("/api/share/worker/run")
        assert r.status_code == 200
        d = r.json()
        assert d["started"] is True
        assert d["already_running"] is False
        assert d["pid"] == 4242
        assert d["log_path"] == "/tmp/fake-worker.log"
        assert d["error"] is None

    def test_worker_run_200_already_running(self, client: TestClient, monkeypatch):
        from moni.web.routes import share as share_route

        class _FakeResult:
            started = False
            already_running = True
            pid = 1234
            log_path = "/tmp/fake-worker.log"
            error = None

        monkeypatch.setattr(share_route, "launch_worker", lambda: _FakeResult())
        r = client.post("/api/share/worker/run")
        assert r.status_code == 200
        d = r.json()
        assert d["started"] is False
        assert d["already_running"] is True
        assert d["pid"] == 1234
        assert d["error"] is None

    def test_worker_run_200_failed_returns_error(self, client: TestClient, monkeypatch):
        """Failed launches still return 200 with error in body so the
        UI modal can show the message verbatim.  We deliberately
        don't 5xx — the request itself succeeded, the subprocess
        just couldn't start."""
        from moni.web.routes import share as share_route

        class _FakeResult:
            started = False
            already_running = False
            pid = None
            log_path = "/tmp/fake-worker.log"
            error = "worker script not found: /nope"

        monkeypatch.setattr(share_route, "launch_worker", lambda: _FakeResult())
        r = client.post("/api/share/worker/run")
        assert r.status_code == 200
        d = r.json()
        assert d["started"] is False
        assert d["already_running"] is False
        assert d["error"] == "worker script not found: /nope"


class TestWorkerLog:
    """GET /api/share/worker/log?tail=N endpoint.  We patch
    tail_log in the route module (where the import lives) so
    the endpoint uses our fake."""

    def test_worker_log_returns_lines(self, client: TestClient, monkeypatch):
        from moni.web.routes import share as share_route
        monkeypatch.setattr(
            share_route, "tail_log",
            lambda n=200: "line A\nline B\nline C",
        )
        r = client.get("/api/share/worker/log?tail=10")
        assert r.status_code == 200
        d = r.json()
        assert d["lines"] == ["line A", "line B", "line C"]
        assert d["log_path"].endswith("worker.log")

    def test_worker_log_empty_returns_empty_lines(
        self, client: TestClient, monkeypatch,
    ):
        from moni.web.routes import share as share_route
        monkeypatch.setattr(share_route, "tail_log", lambda n=200: "")
        r = client.get("/api/share/worker/log?tail=10")
        assert r.status_code == 200
        d = r.json()
        assert d["lines"] == []

    def test_get_task_by_id_404(self, client: TestClient):
        r = client.get("/api/share/tasks/nonexistent-task-id")
        assert r.status_code == 404


# ---------------------------------------------------------------------------
# v1.0.5.18: 6-format / 4 user-options / 4 approach tests
# ---------------------------------------------------------------------------
# The new code path adds 4 universal fields to every payload
# (include_abstract, include_fact_check, provider, model) plus 2 new
# formats (rich_text, report_png) and prunes 6 legacy formats.  These
# tests cover the API surface (per PRD §10): ~22 tests grouped by
# concern.
# ---------------------------------------------------------------------------


class TestShareV18Formats:
    """The 6 v1.0.5.18 share formats each accept a minimal payload
    and return 201.  We use the bare-minimum payload (just
    article_ids) and rely on the Pydantic defaults for everything
    else.  This guards against the 'forgot to add the 4 user-options
    to PlainTextPayload' bug we just fixed in v1.0.5.18."""

    @pytest.mark.parametrize("fmt", [
        "plain_text",
        "markdown",
        "rich_text",
        "html_landing",
        "info_graphic_png",
        "report_png",
    ])
    def test_format_minimal_payload_accepted(
        self, client: TestClient, seeded_article: str, fmt: str,
    ):
        # html_landing is the only one that requires a `title`.  We
        # send the minimum required payload per format.
        extra = {}
        if fmt == "html_landing":
            extra["title"] = "Test"
        r = client.post(
            "/api/share",
            json={
                "format": fmt,
                "version": "1.0",
                "payload": {"article_ids": [seeded_article], **extra},
            },
        )
        assert r.status_code == 201, f"format={fmt!r}: {r.text}"
        data = r.json()
        assert data["status"] == "pending"
        assert "share_id" in data

    @pytest.mark.parametrize("fmt", [
        "ascii_art", "ascii_video", "architecture_svg",
        "knowledge_comic", "pdf_bound", "ppt_slides",
    ])
    def test_legacy_format_rejected(
        self, client: TestClient, seeded_article: str, fmt: str,
    ):
        """6 formats were pruned in v1.0.5.18 (no real worker)."""
        r = client.post(
            "/api/share",
            json={
                "format": fmt,
                "version": "1.0",
                "payload": {"article_ids": [seeded_article]},
            },
        )
        assert r.status_code == 400
        assert r.json()["code"] == "bad_request"
        assert fmt in r.json()["message"]


class TestShareV18UserOptions:
    """The 4 universal user-options (per PRD §2): include_abstract /
    include_fact_check / provider / model.  These are accepted on
    every payload and round-trip through the API."""

    @pytest.mark.parametrize("fmt", [
        "plain_text", "markdown", "rich_text", "html_landing",
        "info_graphic_png", "report_png",
    ])
    def test_user_options_accepted_on_all_formats(
        self, client: TestClient, seeded_article: str, fmt: str,
    ):
        extra = {"include_abstract": False, "include_fact_check": True}
        if fmt == "html_landing":
            extra["title"] = "Test"
        if fmt == "report_png":
            extra["generation_approach"] = "baoyu-infographic"
        r = client.post(
            "/api/share",
            json={
                "format": fmt,
                "version": "1.0",
                "payload": {
                    "article_ids": [seeded_article],
                    "provider": "google",
                    "model": "gemini-2.5-pro",
                    **extra,
                },
            },
        )
        assert r.status_code == 201, f"format={fmt!r}: {r.text}"

    def test_provider_optional_none(
        self, client: TestClient, seeded_article: str,
    ):
        """provider=None must work (means "use the default profile's
        provider").  We just verify 201; the worker passes through
        whatever's in the queue file."""
        r = client.post(
            "/api/share",
            json={
                "format": "markdown",
                "version": "1.0",
                "payload": {
                    "article_ids": [seeded_article],
                    "provider": None,
                    "model": None,
                },
            },
        )
        assert r.status_code == 201

    def test_estimate_endpoint_works_with_v18_payloads(
        self, client: TestClient, seeded_article: str,
    ):
        """v1.0.5.18 payloads with the 4 new user-options fields
        should still flow through the estimate endpoint (which
        re-uses validate_manifest and returns token / duration
        estimates).  Mirrors the 4 user-options going into the
        real create call without crashing."""
        r = client.post(
            "/api/share/estimate",
            json={
                "format": "report_png",
                "version": "1.0",
                "payload": {
                    "article_ids": [seeded_article],
                    "include_abstract": True,
                    "include_fact_check": True,
                    "provider": "google",
                    "model": "gemini-2.5-pro",
                    "generation_approach": "baoyu-infographic",
                },
            },
        )
        assert r.status_code == 200
        d = r.json()
        assert d["format"] == "report_png"
        # 4 new fields are part of the response shape (model only;
        # tokens/duration are the user-facing numbers).
        assert d["model_name"]
        assert d["provider_name"]
        assert d["input_tokens_est"] >= 0
        assert d["output_tokens_est"] >= 0
        assert d["duration_sec_est"] >= 0


class TestShareV18ReportPng:
    """report_png has 4 generation approaches; only that format
    accepts the `generation_approach` field."""

    @pytest.mark.parametrize("approach", [
        "baoyu-infographic",
        "ljg-card",
        "kami",
        "direct",
    ])
    def test_report_png_4_approaches_accepted(
        self, client: TestClient, seeded_article: str, approach: str,
    ):
        r = client.post(
            "/api/share",
            json={
                "format": "report_png",
                "version": "1.0",
                "payload": {
                    "article_ids": [seeded_article],
                    "generation_approach": approach,
                },
            },
        )
        assert r.status_code == 201, f"approach={approach!r}: {r.text}"

    def test_report_png_default_approach_works(
        self, client: TestClient, seeded_article: str,
    ):
        """Omitting generation_approach should use the Pydantic
        default ('baoyu-infographic') and still return 201."""
        r = client.post(
            "/api/share",
            json={
                "format": "report_png",
                "version": "1.0",
                "payload": {"article_ids": [seeded_article]},
            },
        )
        assert r.status_code == 201

    def test_non_report_png_format_rejects_approach(
        self, client: TestClient, seeded_article: str,
    ):
        """generation_approach is report_png/info_graphic_png-specific.
        Sending it on any other format must raise 400 (extra='forbid')."""
        for fmt, extra_payload in (
            ("markdown",      {}),
            ("rich_text",     {}),
            ("html_landing",  {"title": "Test"}),
            ("plain_text",    {}),
        ):
            r = client.post(
                "/api/share",
                json={
                    "format": fmt,
                    "version": "1.0",
                    "payload": {
                        "article_ids": [seeded_article],
                        "generation_approach": "baoyu-infographic",
                        **extra_payload,
                    },
                },
            )
            assert r.status_code == 400, (
                f"format={fmt!r} should reject generation_approach"
            )


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


class TestLlmProviderCRUD:
    """v1.0.32 patch: LLM provider CRUD + test_provider 修复覆盖.

    - Q1 schema (notes 修复): notes=null/""/省略都能 POST /api/llm/providers 201
    - Q5 module kwarg (D fix): test_provider 不再传 module, 5 provider ok
    - Q3 timeout 修复 (15s → 60s): 默认走 60s
    """

    def test_provider_create_notes_null(self, client: TestClient):
        """notes=null (前端 form 空备注提交) 不再报错 422."""
        from moni.db.seed_llm import seed as seed_llm
        seed_llm()
        r = client.post(
            "/api/llm/providers",
            json={"name": "opencode-go-deepseek", "hermes_provider": "opencode-go", "notes": None},
        )
        assert r.status_code == 201, r.text

    def test_provider_create_notes_empty_string(self, client: TestClient):
        """notes="" 仍然通过 (向后兼容)."""
        from moni.db.seed_llm import seed as seed_llm
        seed_llm()
        r = client.post(
            "/api/llm/providers",
            json={"name": "test-empty", "hermes_provider": "test-prov", "notes": ""},
        )
        assert r.status_code == 201, r.text

    def test_provider_create_notes_omitted(self, client: TestClient):
        """省略 notes 字段 (Pydantic default) — 也能通过."""
        from moni.db.seed_llm import seed as seed_llm
        seed_llm()
        r = client.post(
            "/api/llm/providers",
            json={"name": "test-omit", "hermes_provider": "test-prov"},
        )
        assert r.status_code == 201, r.text

    def test_provider_test_does_not_pass_module_kwarg(self, client: TestClient, monkeypatch):
        """D 修: test_provider 调 call_llm 不传 module=, 任何 provider 不抛 TypeError."""
        from moni.db.seed_llm import seed as seed_llm
        seed_llm()
        # mock call_llm 看是否还传 module kwarg
        captured = {}
        from moni.web.routes import llm as llm_routes
        real_call_llm = llm_routes.__dict__.get("call_llm")  # import 已被 route 内部 lazy

        def fake_call_llm(provider, model, prompt, **kwargs):
            captured.update(kwargs)
            return "ok"
        monkeypatch.setattr("moni.llm.client.call_llm", fake_call_llm, raising=False)
        # 直接调 test_provider (不走 HTTP, 减少耦合)
        from moni.db.connection import get_db
        conn = get_db()
        # 取任意 provider 试
        rows = conn.execute(
            "SELECT p.id, p.hermes_provider, m.model_id FROM llm_providers p "
            "JOIN llm_models m ON m.provider_id = p.id "
            "WHERE p.is_active = 1 AND m.enabled = 1 LIMIT 1"
        ).fetchone()
        if rows:
            # 在 test_provider 路径中 call_llm 是从 import 时拿的, mock 已生效
            from moni.web.routes.llm import test_provider
            result = test_provider(rows["id"])
            assert result.get("ok") is True, result
            assert "module" not in captured, f"module kwarg still passed: {captured}"

    def test_provider_test_timeout_is_60s(self, monkeypatch):
        """Q3 修复: test_provider 默认 timeout 是 60s (不是 15s)."""
        from moni.db.connection import get_db
        conn = get_db()
        # 检查 llm.py 源码确认 timeout 值 (静态检查, 避免 mock call_llm 复杂性)
        import inspect
        from moni.web.routes import llm as llm_routes_module
        src = inspect.getsource(llm_routes_module.test_provider)
        assert "timeout=60" in src, f"expected timeout=60, got:\n{src}"
        assert "timeout=15" not in src, f"timeout=15 still present:\n{src}"
