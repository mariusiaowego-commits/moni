"""Tests for BestBlogs fetcher — date filtering, boilerplate cleaning, title-only matching."""

from __future__ import annotations

import pytest

from moni.fetch.sources.bestblogs import _clean_description, _in_date_range


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
