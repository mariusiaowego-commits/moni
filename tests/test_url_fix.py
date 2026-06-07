"""Unit tests for v1.0.3 qbitai URL fix.

v1.0.5.8: trimmed down to just the qbitai half — the 36kr_newsflash
half was removed alongside the source itself.  We kept the qbitai
fixtures and tests because the RSS-feed URL fix from v1.0.3 is
still relevant (qbitai's homepage still 403s without a User-Agent).

These tests don't hit the network — they feed cached HTML/XML
fixtures into the fetchers by monkey-patching ``httpx.get`` and
verify the parsing logic.

Why fixtures? qbitai's homepage returns 403 without a User-Agent.
The fixtures let us test the parser changes deterministically and
document what a successful response looks like.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

# Ensure the worktree's moni package is importable
WT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(WT_ROOT))


# ── Fixtures ────────────────────────────────────────────────────────────────

# qbitai RSS feed: 2 items. Mirrors the live feed structure (verified 2026-06-06).
CACHED_QBITAI_RSS = """\
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
  xmlns:dc="http://purl.org/rss/1.0/modules/content/"
  xmlns:atom="http://www.w3.org/2005/Atom">
<channel>
  <title>\u91cf\u5b50\u4f4d</title>
  <link>https://www.qbitai.com</link>
  <description>\u8ffd\u8e2a\u4eba\u5de5\u667a\u80fd\u65b0\u8d8b\u52bf</description>
  <item>
    <title>\u6709\u4eba\u9760CPU\u628aAI\u7b97\u529b\u5bc6\u5ea6\u5377\u5230\u4e86\u65b0\u9ad8\u5ea6</title>
    <link>https://www.qbitai.com/2026/06/431045.html</link>
    <pubDate>Fri, 05 Jun 2026 13:16:53 +0000</pubDate>
    <description><![CDATA[Agentic AI\u7684\u7b97\u529b\u7126\u8651]]></description>
  </item>
  <item>
    <title>\u667a\u6e90&amp;\u6e05\u534e\u5408\u4f5c\u6210\u679c\u767b\u4e0aScience</title>
    <link>https://www.qbitai.com/2026/06/431033.html</link>
    <pubDate>Fri, 05 Jun 2026 10:53:01 +0000</pubDate>
    <description><![CDATA[\u8111\u79d1\u5b66\u591a\u6a21\u6001\u57fa\u7840\u6a21\u578b]]></description>
  </item>
</channel>
</rss>
"""


class _FakeResp:
    def __init__(self, text, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


# ── qbitai tests ────────────────────────────────────────────────────────────


class TestQbitaiParser:
    """qbitai v1.0.3 — switched from HTML scraping to RSS feed."""

    def test_fetch_extracts_items_from_rss(self):
        from moni.fetch.sources.qbitai import FetcherQbitai

        with patch(
            "moni.fetch.sources.qbitai.httpx.get",
            return_value=_FakeResp(CACHED_QBITAI_RSS),
        ):
            fetcher = FetcherQbitai()
            items = fetcher.fetch()

        assert len(items) == 2, f"expected 2 items, got {len(items)}"
        first = items[0]
        assert "\u6709\u4eba\u9760CPU" in first["title"]
        assert first["url"] == "https://www.qbitai.com/2026/06/431045.html"
        assert first["source"] == "qbitai"
        assert first["published_at"] is not None
        assert "2026-06-05" in first["published_at"]

    def test_parse_returns_normalised_dict(self):
        from moni.fetch.sources.qbitai import FetcherQbitai

        fetcher = FetcherQbitai()

        result = fetcher.parse({
            "title": "t",
            "url": "https://www.qbitai.com/2026/06/123.html",
            "published_at": "2026-06-05T10:00:00+00:00",
            "content": "c",
        })
        assert result is not None
        assert result["source"] == "qbitai"

        # Missing url
        result = fetcher.parse({"title": "t", "url": ""})
        assert result is None

    def test_url_constant_points_to_rss_feed(self):
        """FEED_URL should be the RSS feed, not the Cloudflare-protected homepage."""
        from moni.fetch.sources import qbitai

        assert qbitai.FEED_URL == "https://www.qbitai.com/feed", (
            f"expected qbitai FEED_URL=https://www.qbitai.com/feed, got {qbitai.FEED_URL!r}"
        )


# ── Header check ────────────────────────────────────────────────────────────


class TestFetcherHeaders:
    """qbitai fetcher should send realistic User-Agent headers."""

    def test_qbitai_uses_user_agent(self):
        from moni.fetch.sources.qbitai import FetcherQbitai

        with patch(
            "moni.fetch.sources.qbitai.httpx.get",
            return_value=_FakeResp(CACHED_QBITAI_RSS),
        ) as mock_get:
            FetcherQbitai().fetch()

        _, kwargs = mock_get.call_args
        headers = kwargs.get("headers", {})
        assert "User-Agent" in headers
        assert "Mozilla" in headers["User-Agent"]
