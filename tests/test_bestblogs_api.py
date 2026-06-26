"""tests/test_bestblogs_api.py — unit tests for BestBlogs API client.

v1.0.9.4: 更新测试以匹配新的 GET /resources/{id} 端点。
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.expanduser("~/dev/moni"))

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate

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

def _seed_article(conn, source="bestblogs", bb_fetched_at=None, bb_resource_id=None):
    """Insert a test article."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO articles (id, title, url, source, raw_content, fetched_at, bb_fetched_at, bb_resource_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("test-art-1", "Test Article", "https://mp.weixin.qq.com/s/test", source, "content here", now, bb_fetched_at, bb_resource_id),
    )
    conn.commit()


class TestFetchResource:
    @patch("moni.fetch.bestblogs_api.httpx.get")
    def test_fetch_resource_success(self, mock_get):
        from moni.fetch.bestblogs_api import fetch_resource

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "success": True,
            "data": {
                "score": 92,
                "summary": "Test summary",
                "oneSentenceSummary": "One line",
                "mainPoints": [{"point": "P1", "explanation": "E1"}],
                "keyQuotes": ["Quote 1"],
                "tags": ["AI", "Tech"],
            },
        }
        mock_get.return_value = mock_resp

        result = fetch_resource("RAW_abc123", "test-key")
        assert result is not None
        assert result["bb_score"] == 92
        assert result["bb_summary"] == "Test summary"
        assert result["bb_one_sentence"] == "One line"
        assert len(result["bb_main_points"]) == 1
        assert len(result["bb_key_quotes"]) == 1
        assert result["bb_tags"] == ["AI", "Tech"]
        assert result["bb_resource_id"] == "RAW_abc123"

    @patch("moni.fetch.bestblogs_api.httpx.get")
    def test_fetch_resource_not_found(self, mock_get):
        from moni.fetch.bestblogs_api import fetch_resource

        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_get.return_value = mock_resp

        result = fetch_resource("RAW_nonexistent", "test-key")
        assert result is None

    @patch("moni.fetch.bestblogs_api.httpx.get")
    def test_fetch_resource_rate_limit(self, mock_get):
        from moni.fetch.bestblogs_api import fetch_resource

        mock_resp = MagicMock()
        mock_resp.status_code = 429
        mock_get.return_value = mock_resp

        result = fetch_resource("RAW_abc123", "test-key")
        assert result is None


class TestFetchAndStore:
    @patch("moni.fetch.bestblogs_api._get_api_key", return_value=None)
    def test_no_api_key(self, mock_key):
        from moni.fetch.bestblogs_api import fetch_and_store

        conn = get_db()
        _seed_article(conn, bb_resource_id="RAW_abc123")
        result = fetch_and_store("test-art-1")
        assert result is None

    def test_non_bestblogs_source(self):
        from moni.fetch.bestblogs_api import fetch_and_store

        conn = get_db()
        _seed_article(conn, source="36kr_rss", bb_resource_id="RAW_abc123")

        with patch("moni.fetch.bestblogs_api._get_api_key", return_value="test-key"):
            result = fetch_and_store("test-art-1")
        assert result is None

    def test_bestblog_subprefix_sources_allowed(self):
        """v1.0.9.3: bestblog-podcast / bestblog-x / bestblog-yt must
        also get BB enhancement.  Old code used `== 'bestblogs'` which
        silently excluded all 156 sub-source articles."""
        from moni.fetch.bestblogs_api import fetch_and_store

        for sub in ("bestblog-podcast", "bestblog-x", "bestblog-yt"):
            conn = get_db()
            # Reset article for this sub-source
            conn.execute("DELETE FROM articles WHERE id = 'test-art-1'")
            _seed_article(conn, source=sub, bb_resource_id="RAW_abc123")

            with patch("moni.fetch.bestblogs_api.fetch_resource") as mock_fetch, \
                 patch("moni.fetch.bestblogs_api._get_api_key", return_value="test-key"):
                mock_fetch.return_value = {
                    "bb_score": 90, "bb_summary": "x", "bb_one_sentence": "x",
                    "bb_main_points": [{"point": "p"}],
                    "bb_key_quotes": ["q"], "bb_tags": ["t"],
                    "bb_resource_id": "RAW_abc123",
                }
                result = fetch_and_store("test-art-1")
                assert result is not None, f"{sub} should produce a result"
                assert mock_fetch.called, f"{sub} should have called fetch_resource"
                # And bb_fetched_at must be set
                row = conn.execute(
                    "SELECT bb_fetched_at FROM articles WHERE id='test-art-1'"
                ).fetchone()
                assert row["bb_fetched_at"] is not None, f"{sub} should mark fetched_at"

    def test_already_fetched(self):
        from moni.fetch.bestblogs_api import fetch_and_store

        conn = get_db()
        _seed_article(conn, bb_fetched_at="2026-01-01T00:00:00+00:00", bb_resource_id="RAW_abc123")
        # Set bb_score so existing data is returned
        conn.execute("UPDATE articles SET bb_score = 85 WHERE id = 'test-art-1'")
        conn.commit()

        with patch("moni.fetch.bestblogs_api._get_api_key", return_value="test-key"):
            result = fetch_and_store("test-art-1")
        assert result is not None
        assert result["bb_score"] == 85

    @patch("moni.fetch.bestblogs_api.fetch_resource")
    @patch("moni.fetch.bestblogs_api._get_api_key", return_value="test-key")
    def test_fetch_and_store_success(self, mock_key, mock_fetch):
        from moni.fetch.bestblogs_api import fetch_and_store

        conn = get_db()
        _seed_article(conn, bb_resource_id="RAW_abc123")

        mock_fetch.return_value = {
            "bb_score": 95,
            "bb_summary": "Great article",
            "bb_one_sentence": "One line insight",
            "bb_main_points": [{"point": "P1", "explanation": "E1"}],
            "bb_key_quotes": ["Quote 1"],
            "bb_tags": ["AI"],
            "bb_resource_id": "RAW_abc123",
        }

        result = fetch_and_store("test-art-1")
        assert result is not None
        assert result["bb_score"] == 95

        # Verify DB was updated
        row = conn.execute(
            "SELECT bb_score, bb_summary, bb_fetched_at FROM articles WHERE id = 'test-art-1'"
        ).fetchone()
        assert row["bb_score"] == 95
        assert row["bb_summary"] == "Great article"
        assert row["bb_fetched_at"] is not None

    @patch("moni.fetch.bestblogs_api._get_api_key", return_value="test-key")
    def test_no_bb_resource_id_marks_fetched(self, mock_key):
        """v1.0.9.4: 文章没有 bb_resource_id (RSS <guid> 缺失) 时，
        标记 bb_fetched_at 避免重试。"""
        from moni.fetch.bestblogs_api import fetch_and_store

        conn = get_db()
        _seed_article(conn, bb_resource_id=None)

        result = fetch_and_store("test-art-1")
        assert result is None

        # Should mark bb_fetched_at
        row = conn.execute(
            "SELECT bb_fetched_at FROM articles WHERE id = 'test-art-1'"
        ).fetchone()
        assert row["bb_fetched_at"] is not None

    @patch("moni.fetch.bestblogs_api.fetch_resource", return_value=None)
    @patch("moni.fetch.bestblogs_api._get_api_key", return_value="test-key")
    def test_api_failure_no_mark(self, mock_key, mock_fetch):
        """API 错误时不标记 bb_fetched_at，允许重试。"""
        from moni.fetch.bestblogs_api import fetch_and_store

        conn = get_db()
        _seed_article(conn, bb_resource_id="RAW_abc123")

        result = fetch_and_store("test-art-1")
        assert result is None

        # Should NOT mark bb_fetched_at (allow retry)
        row = conn.execute(
            "SELECT bb_fetched_at FROM articles WHERE id = 'test-art-1'"
        ).fetchone()
        assert row["bb_fetched_at"] is None
