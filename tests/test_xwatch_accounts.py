"""Tests for moni.fetch.xwatch_accounts — OPML resolve + RSS URL extract."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from moni.fetch.xwatch_accounts import (
    extract_tweet_urls_from_rss,
    fetch_account_tweet_urls,
    load_opml_handle_rss,
    normalize_handle,
    resolve_account_rss,
)

FIX = Path(__file__).parent / "fixtures"
OPML = Path(__file__).resolve().parents[1] / "config" / "bestblogs_x.opml"

SAMPLE_RSS = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Demo</title>
  <item>
    <title>hello 微信小程序</title>
    <link>https://x.com/op7418/status/1111111111111111111</link>
    <guid>https://x.com/op7418/status/1111111111111111111</guid>
  </item>
  <item>
    <title>dup</title>
    <link>https://twitter.com/op7418/status/1111111111111111111</link>
  </item>
  <item>
    <title>second</title>
    <description>see https://x.com/foo/status/2222222222222222222</description>
  </item>
</channel></rss>
"""


class TestNormalize:
    def test_strips_at(self):
        assert normalize_handle("@op7418") == "op7418"
        assert normalize_handle("op7418") == "op7418"


class TestExtractRss:
    def test_extracts_and_dedupes(self):
        urls = extract_tweet_urls_from_rss(SAMPLE_RSS, max_results=10)
        assert len(urls) == 2
        assert urls[0].endswith("/1111111111111111111")
        assert urls[1].endswith("/2222222222222222222")

    def test_max_results(self):
        urls = extract_tweet_urls_from_rss(SAMPLE_RSS, max_results=1)
        assert len(urls) == 1

    def test_empty(self):
        assert extract_tweet_urls_from_rss("") == []
        assert extract_tweet_urls_from_rss("<rss></rss>") == []


class TestOpmlResolve:
    @pytest.mark.skipif(not OPML.is_file(), reason="bestblogs_x.opml missing")
    def test_loads_real_opml(self):
        # Bust cache with path string
        load_opml_handle_rss.cache_clear()
        m = load_opml_handle_rss(str(OPML))
        assert len(m) >= 50
        assert "op7418" in m
        assert m["op7418"].startswith("http")

    @pytest.mark.skipif(not OPML.is_file(), reason="bestblogs_x.opml missing")
    def test_resolve_prefers_account_rss_override(self):
        load_opml_handle_rss.cache_clear()
        url = resolve_account_rss(
            "op7418",
            account_rss={"op7418": "https://example.com/rss"},
            opml_path=str(OPML),
        )
        assert url == "https://example.com/rss"

    @pytest.mark.skipif(not OPML.is_file(), reason="bestblogs_x.opml missing")
    def test_resolve_from_opml(self):
        load_opml_handle_rss.cache_clear()
        url = resolve_account_rss("op7418", opml_path=str(OPML))
        assert url and "xgo.ing" in url


class TestFetchAccountTweetUrls:
    def test_rss_channel(self):
        def fake_get(url, **kw):
            r = MagicMock()
            r.status_code = 200
            r.text = SAMPLE_RSS
            r.raise_for_status = MagicMock()
            return r

        with patch("moni.fetch.xwatch_accounts.httpx.get", fake_get):
            urls, channel = fetch_account_tweet_urls(
                "op7418",
                account_rss={"op7418": "https://example.com/feed.xml"},
                allow_ddg_fallback=False,
            )
        assert channel == "rss"
        assert len(urls) == 2

    def test_ddg_fallback(self):
        fake_ddg = MagicMock(return_value=[
            "https://x.com/u/status/9999999999999999999",
        ])
        urls, channel = fetch_account_tweet_urls(
            "unknown_handle_xyz",
            account_rss={},
            opml_path="/nonexistent.opml",
            allow_ddg_fallback=True,
            ddg_search=fake_ddg,
        )
        assert channel == "ddg"
        assert urls[0].endswith("9999999999999999999")
        fake_ddg.assert_called_once()

    def test_rss_http_error_falls_to_ddg(self):
        def boom(url, **kw):
            raise httpx.ConnectError("down")

        fake_ddg = MagicMock(return_value=["https://x.com/u/status/1"])
        with patch("moni.fetch.xwatch_accounts.httpx.get", boom):
            urls, channel = fetch_account_tweet_urls(
                "op7418",
                account_rss={"op7418": "https://example.com/feed.xml"},
                allow_ddg_fallback=True,
                ddg_search=fake_ddg,
            )
        assert channel == "ddg"
        assert len(urls) == 1
