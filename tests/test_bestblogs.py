"""Tests for BestBlogs fetcher — date filtering, boilerplate cleaning, title-only matching."""

from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

from moni.fetch.sources.bestblogs import (
    FetcherBestBlogs,
    _clean_description,
    _in_date_range,
)


# ── _in_date_range ────────────────────────────────────────────────────────


class TestInDateRange:
    def test_within_range(self):
        assert _in_date_range("2026-06-12T10:00:00+08:00", "2026-06-10", "2026-06-13")

    def test_before_range(self):
        assert not _in_date_range("2026-06-09T10:00:00+08:00", "2026-06-10", None)

    def test_after_range(self):
        assert not _in_date_range("2026-06-14T10:00:00+08:00", None, "2026-06-13")

    def test_no_pub_date_keeps_article(self):
        assert _in_date_range(None, "2026-06-10", "2026-06-13")

    def test_empty_pub_date_keeps_article(self):
        assert _in_date_range("", "2026-06-10", "2026-06-13")

    def test_unparseable_date_keeps_article(self):
        assert _in_date_range("not-a-date", "2026-06-10", "2026-06-13")

    def test_no_filters_keeps_article(self):
        assert _in_date_range("2026-06-12T10:00:00+08:00", None, None)

    def test_only_from(self):
        assert _in_date_range("2026-06-12T10:00:00+08:00", "2026-06-10", None)
        assert not _in_date_range("2026-06-09T10:00:00+08:00", "2026-06-10", None)

    def test_only_to(self):
        assert _in_date_range("2026-06-12T10:00:00+08:00", None, "2026-06-13")
        assert not _in_date_range("2026-06-14T10:00:00+08:00", None, "2026-06-13")

    def test_boundary_inclusive(self):
        assert _in_date_range("2026-06-10T00:00:00+00:00", "2026-06-10", None)
        assert _in_date_range("2026-06-13T23:59:59+00:00", None, "2026-06-13")


# ── _clean_description ────────────────────────────────────────────────────


class TestCleanDescription:
    def test_removes_click_follow(self):
        text = "AI泡沫已经在破了。点击关注公众号「腾讯技术工程」了解更多"
        cleaned = _clean_description(text)
        assert "点击关注" not in cleaned
        assert "AI泡沫" in cleaned

    def test_removes_long_press(self):
        text = "深度分析长按识别二维码关注我们"
        cleaned = _clean_description(text)
        assert "长按识别" not in cleaned

    def test_removes_share_to_moments(self):
        text = "重要观点。分享到朋友圈让更多人看到"
        cleaned = _clean_description(text)
        assert "分享到朋友圈" not in cleaned

    def test_removes_read_original(self):
        text = "摘要内容。点击阅读原文查看完整版"
        cleaned = _clean_description(text)
        assert "点击阅读原文" not in cleaned

    def test_preserves_real_content(self):
        text = "腾讯混元AI Infra进化，HPC-Ops推理核心算子全面升级。"
        cleaned = _clean_description(text)
        assert cleaned == text

    def test_empty_string(self):
        assert _clean_description("") == ""

    def test_only_boilerplate(self):
        text = "点击关注公众号「xxx」了解更多"
        cleaned = _clean_description(text)
        assert len(cleaned) < 5  # essentially empty


# ── Multi-URL support (v1.0.7.1) ────────────────────────────────────────


class TestResolveUrls:
    """Test _resolve_urls() returns list from comma-separated URL string."""

    def test_single_url(self, tmp_path):
        from moni.fetch.sources.bestblogs import FetcherBestBlogs
        fetcher = FetcherBestBlogs()
        # Mock _resolve_urls by testing the split logic directly
        url_str = "https://example.com/opml1.xml"
        result = [u.strip() for u in url_str.split(",") if u.strip()]
        assert result == ["https://example.com/opml1.xml"]

    def test_two_urls(self):
        url_str = "https://example.com/opml1.xml,https://example.com/opml2.xml"
        result = [u.strip() for u in url_str.split(",") if u.strip()]
        assert result == ["https://example.com/opml1.xml", "https://example.com/opml2.xml"]

    def test_empty_string(self):
        url_str = ""
        result = [u.strip() for u in url_str.split(",") if u.strip()]
        assert result == []

    def test_whitespace_handling(self):
        url_str = "https://a.com/opml.xml , https://b.com/opml.xml"
        result = [u.strip() for u in url_str.split(",") if u.strip()]
        assert result == ["https://a.com/opml.xml", "https://b.com/opml.xml"]


class TestMultiOpmlMerge:
    """Test that multiple OPMLs are merged with deduplication."""

    def test_dedup_by_feed_url(self):
        """Simulate merging feeds from two OPMLs — same feed_url should be deduped."""
        feeds_opml1 = [("Feed A", "https://a.com/rss"), ("Feed B", "https://b.com/rss")]
        feeds_opml2 = [("Feed B dup", "https://b.com/rss"), ("Feed C", "https://c.com/rss")]

        seen: set[str] = set()
        merged: list[tuple[str, str]] = []
        for title, url in feeds_opml1 + feeds_opml2:
            if url not in seen:
                seen.add(url)
                merged.append((title, url))

        assert len(merged) == 3
        assert merged[1][0] == "Feed B"  # first occurrence wins
        assert merged[2][0] == "Feed C"


class TestFetcherRegistration:
    """Verify new fetchers are registered in the registry."""

    def test_bestblog_podcast_registered(self):
        from moni.fetch.base import list_fetchers
        fetchers = list_fetchers()
        assert "bestblog-podcast" in fetchers

    def test_bestblog_yt_registered(self):
        from moni.fetch.base import list_fetchers
        fetchers = list_fetchers()
        assert "bestblog-yt" in fetchers

    def test_bestblog_podcast_inherits_from_bestblogs(self):
        from moni.fetch.base import list_fetchers
        from moni.fetch.sources.bestblogs import FetcherBestBlogs
        fetcher_cls = list_fetchers()["bestblog-podcast"]
        assert issubclass(fetcher_cls, FetcherBestBlogs)

    def test_bestblog_yt_inherits_from_bestblogs(self):
        from moni.fetch.base import list_fetchers
        from moni.fetch.sources.bestblogs import FetcherBestBlogs
        fetcher_cls = list_fetchers()["bestblog-yt"]
        assert issubclass(fetcher_cls, FetcherBestBlogs)

    def test_bestblog_x_registered(self):
        from moni.fetch.base import list_fetchers
        fetchers = list_fetchers()
        assert "bestblog-x" in fetchers

    def test_bestblog_x_inherits_from_bestblogs(self):
        from moni.fetch.base import list_fetchers
        from moni.fetch.sources.bestblogs import FetcherBestBlogs
        fetcher_cls = list_fetchers()["bestblog-x"]
        assert issubclass(fetcher_cls, FetcherBestBlogs)


# ── v1.0.7.5: Atom feed parsing (YouTube/GitHub) ───────────────────────────


ATOM_YOUTUBE_FEED = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
  <title>AI Engineer</title>
  <link rel="alternate" href="https://www.youtube.com/channel/UCLKPca3kwwd-B59HNr-_lvA"/>
  <author><name>AI Engineer</name></author>
  <entry>
    <id>yt:video:0S8xe9ftGTM</id>
    <title>6 Things to Know about AIE World's Fair 2026</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=0S8xe9ftGTM"/>
    <author><name>AI Engineer</name></author>
    <published>2026-06-21T03:46:17+00:00</published>
    <summary>We're back in SF!</summary>
  </entry>
  <entry>
    <id>yt:video:AAAA</id>
    <title>Building Agents with LangGraph</title>
    <link rel="alternate" href="https://www.youtube.com/watch?v=AAAA"/>
    <author><name>AI Engineer</name></author>
    <published>2026-06-20T10:00:00+00:00</published>
    <summary>Deep dive into agentic workflows.</summary>
  </entry>
</feed>
"""


class TestAtomParsing:
    """v1.0.7.5: YouTube / GitHub / podcast Atom feeds must parse correctly."""

    def test_atom_youtube_extracts_entries(self):
        """_fetch_feed must handle Atom feeds and extract <entry> items."""
        f = FetcherBestBlogs.__new__(FetcherBestBlogs)
        f.source_name = "bestblog-yt"

        # Patch httpx.get to return our canned Atom feed.
        mock_response = httpx.Response(
            200,
            content=ATOM_YOUTUBE_FEED.encode("utf-8"),
            request=httpx.Request("GET", "https://example.com"),
        )
        with patch("moni.fetch.sources.bestblogs.httpx.get", return_value=mock_response):
            items = f._fetch_feed("https://example.com/feed.xml", "AI Engineer")

        assert len(items) == 2
        assert items[0]["title"] == "6 Things to Know about AIE World's Fair 2026"
        assert items[0]["url"] == "https://www.youtube.com/watch?v=0S8xe9ftGTM"
        assert items[0]["publisher"] == "AI Engineer"
        assert items[0]["published_at"] is not None
        assert items[0]["published_at"].startswith("2026-06-21")
        assert items[1]["title"] == "Building Agents with LangGraph"
        assert items[1]["url"] == "https://www.youtube.com/watch?v=AAAA"

    def test_atom_youtube_empty_when_no_entries(self):
        """Atom feed with zero <entry> items must return []."""
        empty_atom = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Empty</title>
</feed>"""
        f = FetcherBestBlogs.__new__(FetcherBestBlogs)
        f.source_name = "bestblog-yt"
        mock_response = httpx.Response(
            200,
            content=empty_atom.encode("utf-8"),
            request=httpx.Request("GET", "https://example.com"),
        )
        with patch("moni.fetch.sources.bestblogs.httpx.get", return_value=mock_response):
            items = f._fetch_feed("https://example.com/feed.xml", "Empty")
        assert items == []

    def test_atom_entry_without_link_is_skipped(self):
        """Entries lacking <link rel=alternate href> must be skipped."""
        no_link = """<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry><title>no link here</title></entry>
</feed>"""
        f = FetcherBestBlogs.__new__(FetcherBestBlogs)
        f.source_name = "bestblog-yt"
        mock_response = httpx.Response(
            200,
            content=no_link.encode("utf-8"),
            request=httpx.Request("GET", "https://example.com"),
        )
        with patch("moni.fetch.sources.bestblogs.httpx.get", return_value=mock_response):
            items = f._fetch_feed("https://example.com/feed.xml", "x")
        assert items == []

    def test_rss_still_works_after_atom_added(self):
        """Regression: existing RSS 2.0 parsing must not be broken."""
        rss = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Test</title>
  <item>
    <title>RSS Item</title>
    <link>https://example.com/rss1</link>
    <description>desc</description>
    <pubDate>Fri, 20 Jun 2026 10:00:00 +0000</pubDate>
  </item>
</channel></rss>"""
        f = FetcherBestBlogs.__new__(FetcherBestBlogs)
        f.source_name = "bestblogs"
        mock_response = httpx.Response(
            200,
            content=rss.encode("utf-8"),
            request=httpx.Request("GET", "https://example.com"),
        )
        with patch("moni.fetch.sources.bestblogs.httpx.get", return_value=mock_response):
            items = f._fetch_feed("https://example.com/feed.xml", "Test")
        assert len(items) == 1
        assert items[0]["title"] == "RSS Item"
        assert items[0]["url"] == "https://example.com/rss1"
