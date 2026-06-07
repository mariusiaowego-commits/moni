"""Unit tests for v1.0.3 URL fix (36kr_newsflash + qbitai).

These tests don't hit the network — they feed cached HTML/XML fixtures into
the fetchers by monkey-patching ``httpx.get`` and verify the parsing logic.

Why fixtures? 36kr's edge serves a captcha challenge when it flags the
``httpx`` fingerprint (production reality), and qbitai's homepage returns 403
without a User-Agent. The fixtures let us test the parser changes deterministically
and document what a successful response looks like.
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

# 36kr newsflash: 3 items in <div class=newsflash-item> containers.
# Hand-crafted to mirror the real 36kr page structure (verified against the
# live 102KB download on 2026-06-06).
CACHED_KR_NEWSFLASH_HTML = """\
<!doctype html>
<html data-path="/newsflashes">
<head><title>36kr newsflash</title></head>
<body>
<div class="newsflash-item">
  <a class="item-title" href="/newsflashes/3841105138141443">\u534e\u6cf0\u8bc1\u5238\uff1a5\u6708\u975e\u519c\u518d\u8d85\u9884\u671f\u63a8\u5347\u7f8e\u8054\u50a8\u52a0\u606f\u5fc5\u8981\u6027</a>
  <div class="item-other">
    <div class="item-related"><span class="time">1\u5206\u949f\u524d</span></div>
  </div>
</div>
<div class="newsflash-item">
  <a class="item-title" href="/newsflashes/3841030859081984">SpaceX IPO\u5df2\u83b7\u5f97\u500d\u8ba2\u8d2d\u8d44\u91d1</a>
  <div class="item-other">
    <div class="item-related"><span class="time">1\u5c0f\u65f6\u524d</span></div>
  </div>
</div>
<div class="newsflash-item">
  <a class="item-title" href="/newsflashes/3841029124016387">\u82b1\u65d7\u7ef4\u6301\u5bf9\u7f8e\u8054\u50a8\u5e74\u5185\u964d\u606f3\u6b21\u7684\u9884\u6d4b</a>
  <div class="item-other">
    <div class="item-related"><span class="time">2\u5c0f\u65f6\u524d</span></div>
  </div>
</div>
</body>
</html>
"""

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

# 36kr captcha challenge (1468 bytes, served by 36kr's edge to flagged IPs).
CAPTCHA_HTML = """\
<html>
  <head><link rel="icon" href="data:;base64,="></head>
  <script src="https://lf-cdn-tos.bytescm.com/obj/static/sec_sdk_build/3.3.4/captcha/index.js"></script>
  <script>
    const options = { commonOptions: { aid: 331981 } };
    window.TTGCaptcha.init(options);
    window.TTGCaptcha.render({ verify_data: '{}' });
  </script>
</html>
"""


class _FakeResp:
    def __init__(self, text, status_code: int = 200):
        self.text = text
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise Exception(f"HTTP {self.status_code}")


# ── 36kr_newsflash tests ────────────────────────────────────────────────────


class Test36krNewsflashParser:
    """36kr newsflash v1.0.3 — parse <div class=newsflash-item> containers.

    v1.0.5.5: the fetcher now goes through ``_fetch_via_browser`` first
    (Chrome + body.innerText) before falling back to ``httpx``.  These
    tests mock the browser entry point so the parser logic is exercised
    in isolation, regardless of which fetch path returns the body.
    """

    def test_fetch_extracts_items_from_cached_html(self):
        """The new parser pulls 3 items from a representative 36kr newsflash page."""
        from moni.fetch.sources import kr_newsflash
        from moni.fetch.sources.kr_newsflash import Fetcher36krNewsflash

        with patch.object(
            kr_newsflash, "_fetch_via_browser",
            return_value=CACHED_KR_NEWSFLASH_HTML,
        ):
            fetcher = Fetcher36krNewsflash()
            items = fetcher.fetch()

        assert len(items) == 3, f"expected 3 items, got {len(items)}"
        first = items[0]
        assert first["title"] == (
            "\u534e\u6cf0\u8bc1\u5238\uff1a5\u6708\u975e\u519c\u518d\u8d85\u9884\u671f"
            "\u63a8\u5347\u7f8e\u8054\u50a8\u52a0\u606f\u5fc5\u8981\u6027"
        )
        assert first["url"] == "https://36kr.com/newsflashes/3841105138141443"
        assert first["source"] == "36kr_newsflash"
        assert first["published_at"] is not None  # "1\u5206\u949f\u524d" parsed

    def test_fetch_handles_captcha_page(self):
        """When 36kr serves a captcha, the fetcher returns 0 items and logs a warning."""
        from moni.fetch.sources import kr_newsflash

        with patch(
            "moni.fetch.sources.kr_newsflash.httpx.get",
            return_value=_FakeResp(CAPTCHA_HTML),
        ):
            items = kr_newsflash.Fetcher36krNewsflash().fetch()

        assert items == [], "captcha page should yield 0 items, not crash"

    def test_parse_returns_normalised_dict(self):
        """parse() returns the canonical schema or None for invalid input."""
        from moni.fetch.sources.kr_newsflash import Fetcher36krNewsflash

        fetcher = Fetcher36krNewsflash()

        result = fetcher.parse({
            "title": "test",
            "url": "https://36kr.com/newsflashes/123",
            "published_at": "2026-06-05T10:00:00+00:00",
            "content": "body",
        })
        assert result is not None
        assert result["source"] == "36kr_newsflash"
        assert result["url"].startswith("https://")

        # Invalid (no url) — should be skipped
        result = fetcher.parse({"title": "no url"})
        assert result is None

    def test_parse_time_relative(self):
        """_parse_time handles \u5206\u949f\u524d / \u5c0f\u65f6\u524d / \u6628\u5929 HH:MM."""
        from datetime import datetime, timezone
        from moni.fetch.sources.kr_newsflash import Fetcher36krNewsflash

        fetcher = Fetcher36krNewsflash()

        # "1\u5c0f\u65f6\u524d" -> ~3600s in the past
        ts = fetcher._parse_time("1\u5c0f\u65f6\u524d")
        assert ts is not None
        delta = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
        assert 3500 < delta < 3700, f"expected ~3600s ago, got {delta}"

        # "30\u5206\u949f\u524d" -> ~1800s in the past
        ts = fetcher._parse_time("30\u5206\u949f\u524d")
        assert ts is not None
        delta = (datetime.now(timezone.utc) - datetime.fromisoformat(ts)).total_seconds()
        assert 1700 < delta < 1900, f"expected ~1800s ago, got {delta}"

        # Unparseable
        assert fetcher._parse_time("garbage") is None


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
    """Both fetchers should send realistic User-Agent headers.

    v1.0.5.5: the httpx path now lives in ``_fetch_via_httpx``, not in
    ``Fetcher36krNewsflash.fetch`` directly.  Force the fetcher down
    the httpx path by making ``_fetch_via_browser`` return None, then
    assert on the httpx call's headers (httpx is only invoked by the
    fallback path).
    """

    def test_36kr_newsflash_uses_user_agent(self):
        from moni.fetch.sources import kr_newsflash
        from moni.fetch.sources.kr_newsflash import Fetcher36krNewsflash

        with patch.object(kr_newsflash, "_fetch_via_browser", return_value=None), \
             patch("httpx.get",
                   return_value=_FakeResp(CACHED_KR_NEWSFLASH_HTML)) as mock_get:
            Fetcher36krNewsflash().fetch()

        _, kwargs = mock_get.call_args
        headers = kwargs.get("headers", {})
        assert "User-Agent" in headers, "should send User-Agent"
        assert "Mozilla" in headers["User-Agent"], "UA should be browser-like"

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
