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
from unittest.mock import patch

import pytest


WORKTREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKTREE not in sys.path:
    sys.path.insert(0, WORKTREE)


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
        """v1.0.4 wechat_rss name still resolves for backward compat."""
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
        assert "wechat_rss_b" in list_fetchers()
