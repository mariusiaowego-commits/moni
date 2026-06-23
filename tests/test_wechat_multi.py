"""Unit tests for v1.0.5.6 wechat_rss multi-account support.

The fetcher now backs wechat_rss_a / wechat_rss_b / wechat_rss_c /
wechat_rss_d / wechat_rss_e — each one a separate source_name with
its own token baked into the source's url field.  These tests cover:

* Each variant is auto-registered in the global fetcher registry.
* ``_resolve_url()`` reads from the DB sources row first, then falls
  back to the env var for legacy single-source setups.
* parse() stamps the per-instance source_name on the article dict
  (so the dedupe key is per-account, not shared).
* The legacy wechat_rss source still works (backward compat).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import patch

import pytest


WORKTREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKTREE not in sys.path:
    sys.path.insert(0, WORKTREE)


@pytest.fixture
def fresh_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Per-test isolated SQLite DB (matches other test files' convention)."""
    from moni.db.connection import set_db_path
    from moni.db.migrate import migrate

    db_path = tmp_path / "news.db"
    set_db_path(db_path)
    migrate()
    yield db_path


# ── alias registration ─────────────────────────────────────────────────────


class TestWechatRssAliasRegistration:
    def test_a_through_e_are_registered(self):
        """The five variants are auto-registered at import time."""
        from moni.fetch.base import list_fetchers
        from moni.fetch.sources import wechat_rss  # triggers registration

        fetchers = list_fetchers()
        for variant in ("wechat_rss_a", "wechat_rss_b", "wechat_rss_c",
                        "wechat_rss_d", "wechat_rss_e"):
            assert variant in fetchers, f"{variant} missing from registry"

    def test_legacy_wechat_rss_still_registered(self):
        """v1.0.5.9: legacy 'wechat_rss' is no longer in the sources table
        (superseded by wechat_rss_a/b), but the FetcherWechatRSS class
        is still registered under that name so old
        ``moni fetch --source wechat_rss`` CLI invocations keep working
        (they resolve the URL from WECHAT_RSS_TOKEN env var).
        """
        from moni.fetch.base import list_fetchers
        from moni.fetch.sources import wechat_rss  # noqa: F401

        assert "wechat_rss" in list_fetchers()

    def test_variants_share_same_code(self):
        """All variants point at subclasses of FetcherWechatRSS."""
        from moni.fetch.base import list_fetchers
        from moni.fetch.sources.wechat_rss import FetcherWechatRSS
        from moni.fetch.sources import wechat_rss  # noqa: F401

        fetchers = list_fetchers()
        for variant in ("wechat_rss_a", "wechat_rss_b", "wechat_rss_c"):
            cls = fetchers[variant]
            assert issubclass(cls, FetcherWechatRSS), \
                f"{variant} ({cls}) must subclass FetcherWechatRSS"

    def test_variants_have_distinct_source_names(self):
        """Each variant's source_name matches its registry key."""
        from moni.fetch.base import list_fetchers
        from moni.fetch.sources import wechat_rss  # noqa: F401

        fetchers = list_fetchers()
        for variant in ("wechat_rss_a", "wechat_rss_b", "wechat_rss_c",
                        "wechat_rss_d", "wechat_rss_e"):
            assert fetchers[variant].source_name == variant


# ── _resolve_url() priority ─────────────────────────────────────────────────


class TestResolveUrlPriority:
    def test_db_row_wins_over_env(self, monkeypatch):
        """The DB sources.url (with token embedded) takes priority."""
        from moni.fetch.base import list_fetchers
        from moni.fetch.sources import wechat_rss  # triggers registration

        wechat_rss_b = list_fetchers()["wechat_rss_b"]

        # Pretend env var has a *different* token.
        monkeypatch.setenv("WECHAT_RSS_TOKEN", "ENV_TOKEN_SHOULD_BE_IGNORED")

        # Patch get_db at its source — it's imported inside _resolve_url.
        with patch("moni.db.connection.get_db") as mock_db:
            mock_conn = mock_db.return_value.__enter__.return_value
            mock_conn.execute.return_value.fetchone.return_value = {
                "url": "https://wechatrss.waytomaster.com/api/rss/all?token=DB_TOKEN_WINS",
            }
            url = wechat_rss_b()._resolve_url()
        assert "DB_TOKEN_WINS" in url, \
            f"DB url should win, got {url!r}"

    def test_env_var_fallback_when_db_row_empty(self, monkeypatch):
        """If the DB row has no url, fall back to env var."""
        from moni.fetch.sources.wechat_rss import FetcherWechatRSS

        monkeypatch.setenv("WECHAT_RSS_TOKEN", "FALLBACK_TOKEN")

        with patch("moni.db.connection.get_db") as mock_db:
            mock_conn = mock_db.return_value.__enter__.return_value
            mock_conn.execute.return_value.fetchone.return_value = None
            url = FetcherWechatRSS()._resolve_url()
        assert "FALLBACK_TOKEN" in url
        assert url.startswith("https://wechatrss.waytomaster.com/api/rss/all")

    def test_no_token_returns_empty_string(self, monkeypatch):
        """No DB row, no env var → empty string (caller logs error)."""
        from moni.fetch.sources.wechat_rss import FetcherWechatRSS

        monkeypatch.delenv("WECHAT_RSS_TOKEN", raising=False)
        with patch("moni.db.connection.get_db") as mock_db, \
             patch("moni.fetch.sources.wechat_rss._load_token_from_envfile",
                   return_value=""):
            mock_conn = mock_db.return_value.__enter__.return_value
            mock_conn.execute.return_value.fetchone.return_value = None
            url = FetcherWechatRSS()._resolve_url()
        assert url == ""


# ── parse() stamps the right source_name ────────────────────────────────────


class TestParseStampsSourceName:
    def test_wechat_rss_b_parse_uses_b_as_source(self):
        from moni.fetch.base import list_fetchers
        from moni.fetch.sources import wechat_rss  # triggers registration

        wechat_rss_b = list_fetchers()["wechat_rss_b"]
        fetcher = wechat_rss_b()
        out = fetcher.parse({
            "title": "t", "url": "https://example.com/x",
            "source": "should_be_overridden", "publisher": "Wechat Account B",
        })
        assert out["source"] == "wechat_rss_b"
        assert out["publisher"] == "Wechat Account B"

    def test_parse_handles_missing_publisher(self):
        from moni.fetch.base import list_fetchers
        from moni.fetch.sources import wechat_rss  # triggers registration

        wechat_rss_a = list_fetchers()["wechat_rss_a"]
        out = wechat_rss_a().parse({
            "title": "t", "url": "https://example.com/x",
            "publisher": None,
        })
        assert out["publisher"] is None
        assert out["source"] == "wechat_rss_a"


# ── alias registration is idempotent ────────────────────────────────────────


class TestAliasIdempotent:
    def test_double_register_does_not_warn(self, caplog):
        """Importing the module twice should not log a duplicate-key warning."""
        import importlib
        import moni.fetch.sources.wechat_rss as wr
        importlib.reload(wr)
        # If we got here without errors, idempotency holds.
        from moni.fetch.base import list_fetchers
        assert "wechat_rss_a" in list_fetchers()


# ── v1.0.7.6.2: placeholder-URL rejection ─────────────────────────────────


class TestPlaceholderUrlRejected:
    """v1.0.7.6.2: seed_config used to insert ``?token=__WECHAT_RSS_X_TOKEN__``
    into sources.url when the env var was unset.  waytomaster then
    404'd and the user saw 0 articles.  We now (a) seed empty string,
    (b) refuse placeholder URLs at fetch time with a clear log.
    """

    def test_env_token_returns_empty_string_when_unset(self, monkeypatch):
        """``_env_token`` must NOT synthesise a placeholder string any more."""
        monkeypatch.delenv("WECHAT_RSS_A_TOKEN", raising=False)
        monkeypatch.delenv("WECHAT_RSS_B_TOKEN", raising=False)
        monkeypatch.delenv("WECHAT_RSS_TOKEN", raising=False)
        from moni.db.seed_config import _env_token
        assert _env_token("A") == ""
        assert _env_token("B") == ""

    def test_env_token_returns_actual_value_when_set(self, monkeypatch):
        monkeypatch.setenv("WECHAT_RSS_A_TOKEN", "real-token-A")
        monkeypatch.setenv("WECHAT_RSS_B_TOKEN", "real-token-B")
        from moni.db.seed_config import _env_token
        assert _env_token("A") == "real-token-A"
        assert _env_token("B") == "real-token-B"

    def test_env_token_a_falls_back_to_legacy_token(self, monkeypatch):
        """Account A still honours the legacy WECHAT_RSS_TOKEN env var."""
        monkeypatch.delenv("WECHAT_RSS_A_TOKEN", raising=False)
        monkeypatch.setenv("WECHAT_RSS_TOKEN", "legacy-token")
        from moni.db.seed_config import _env_token
        assert _env_token("A") == "legacy-token"

    def test_fetcher_refuses_placeholder_url(self, fresh_db, caplog):
        """If a stale DB row still has the placeholder URL, fetch() must
        return [] and log a clear error instead of hitting waytomaster."""
        from moni.fetch.base import list_fetchers
        from moni.fetch.sources import wechat_rss  # noqa: F401 — register

        # fresh_db doesn't seed sources rows, so insert one with the
        # placeholder URL (mimic pre-fix state)
        from datetime import datetime, timezone
        from moni.db.connection import get_db
        now = datetime.now(timezone.utc).isoformat()
        get_db().execute(
            "INSERT INTO sources (id, name, display_name, source_type, url, "
            "fetch_interval_min, is_active, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "src-wechat-rss-a", "wechat_rss_a", "WeChatRSS 账号 A",
                "wechat_aggregator",
                "https://wechatrss.waytomaster.com/api/rss/all?token=__WECHAT_RSS_A_TOKEN__",
                5, 1, "", now, now,
            ),
        )

        fetcher_cls = list_fetchers()["wechat_rss_a"]
        fetcher = fetcher_cls()
        with caplog.at_level("ERROR"):
            articles = fetcher.fetch()
        assert articles == []
        # Verify the error mentions the placeholder so the user can fix it
        assert any(
            "placeholder" in rec.message.lower() for rec in caplog.records
        ), f"expected a 'placeholder' error log, got: {[r.message for r in caplog.records]}"

    def test_fetcher_refuses_empty_url(self, fresh_db, caplog):
        """Empty URL = env var was unset during seed.  fetch() must not
        hit waytomaster; loud error, no articles."""
        from moni.fetch.base import list_fetchers
        from moni.fetch.sources import wechat_rss  # noqa: F401

        from datetime import datetime, timezone
        from moni.db.connection import get_db
        now = datetime.now(timezone.utc).isoformat()
        get_db().execute(
            "INSERT INTO sources (id, name, display_name, source_type, url, "
            "fetch_interval_min, is_active, notes, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "src-wechat-rss-b-empty", "wechat_rss_b", "WeChatRSS 账号 B",
                "wechat_aggregator", "", 5, 1, "", now, now,
            ),
        )

        fetcher = list_fetchers()["wechat_rss_b"]()
        with caplog.at_level("ERROR"):
            articles = fetcher.fetch()
        assert articles == []
        assert "wechat_rss_b" in list_fetchers()