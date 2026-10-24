"""Offline tests for the Chinese independent blog OPML fetcher."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import httpx
import pytest

from moni.db.connection import close_db, get_db, set_db_path
from moni.db.migrate import migrate
from moni.db.seed_config import seed as seed_config
from moni.fetch.sources.chinese_independent_blogs import (
    FetcherChineseIndependentBlogs,
    _inside_config_root,
    _is_http_url,
    _parse_date,
)


@pytest.fixture
def seeded_db(tmp_path):
    close_db()
    set_db_path(tmp_path / "news.db")
    migrate()
    seed_config()
    yield get_db()
    close_db()


RSS = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>Feed</title>
  <item>
    <title>RSS post</title>
    <link>https://example.com/rss-post</link>
    <description><![CDATA[<p>RSS body</p>]]></description>
    <pubDate>Fri, 20 Jun 2026 10:00:00 +0000</pubDate>
    <author>Alice</author>
  </item>
  <item><title>No link</title><description>x</description></item>
</channel></rss>
"""

ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>Atom</title>
  <entry>
    <title>Atom post</title>
    <link rel="alternate" href="https://example.com/atom-post"/>
    <published>2026-06-21T00:00:00Z</published>
    <author><name>Bob</name></author>
    <content type="html">&lt;p&gt;Atom body&lt;/p&gt;</content>
  </entry>
  <entry><title>No link</title><summary>x</summary></entry>
</feed>
"""

OPML = """<?xml version="1.0"?>
<opml version="1.0"><head><title>t</title></head><body>
  <outline title="A" xmlUrl="https://a.example/feed"/>
  <outline title="B" xmlUrl="http://b.example/feed"/>
  <outline title="File" xmlUrl="file:///etc/passwd"/>
  <outline title="No URL"/>
</body></opml>
"""


class TestHelpers:
    def test_http_urls_only(self):
        assert _is_http_url("https://a")
        assert _is_http_url("http://a")
        assert not _is_http_url("file:///a")
        assert not _is_http_url("javascript:alert(1)")

    def test_parse_rfc822_and_iso(self):
        assert _parse_date("Fri, 20 Jun 2026 10:00:00 +0000") == "2026-06-20T10:00:00+00:00"
        assert _parse_date("2026-06-21T00:00:00Z") == "2026-06-21T00:00:00+00:00"
        assert _parse_date("not a date") is None
        assert _parse_date(None) is None

    def test_config_path_guard(self):
        root = Path(__file__).resolve().parents[1] / "config"
        assert _inside_config_root(root / "chinese_independent_blogs.opml")
        assert not _inside_config_root(root.parent / "README.md")


class TestOpml:
    def test_parse_real_checked_in_opml(self):
        fetcher = FetcherChineseIndependentBlogs()
        path = Path(__file__).resolve().parents[1] / "config" / "chinese_independent_blogs.opml"
        feeds = fetcher._load_opml(str(path))
        assert len(feeds) == 695
        assert len({url for _, url in feeds}) == 695
        assert all(_is_http_url(url) for _, url in feeds)

    def test_skip_non_http_and_empty(self):
        fetcher = FetcherChineseIndependentBlogs()
        with patch.object(fetcher, "_http_text", return_value=OPML):
            feeds = fetcher._load_opml("https://example.com/feed.opml")
        assert feeds == [
            ("A", "https://a.example/feed"),
            ("B", "http://b.example/feed"),
        ]

    def test_remote_opml_failure_returns_empty(self, caplog):
        fetcher = FetcherChineseIndependentBlogs()
        with patch.object(fetcher, "_http_text", side_effect=httpx.ConnectError("nope")):
            assert fetcher._load_opml("https://example.com/feed.opml") == []
        assert "failed to load OPML" in caplog.text

    def test_local_opml_outside_config_rejected(self, tmp_path):
        fetcher = FetcherChineseIndependentBlogs()
        outside = tmp_path / "outside.opml"
        outside.write_text(OPML, encoding="utf-8")
        assert fetcher._load_opml(str(outside)) == []


class TestFeedParsing:
    def test_rss(self):
        fetcher = FetcherChineseIndependentBlogs()
        # Parse directly to keep this test independent of HTTP mocking.
        from xml.etree import ElementTree as ET
        items = fetcher._parse_rss(ET.fromstring(RSS.encode()), "Feed")
        assert len(items) == 1
        assert items[0] == {
            "title": "RSS post",
            "url": "https://example.com/rss-post",
            "published_at": "2026-06-20T10:00:00+00:00",
            "content": "<p>RSS body</p>",
            "publisher": "Alice",
        }

    def test_atom(self):
        fetcher = FetcherChineseIndependentBlogs()
        from xml.etree import ElementTree as ET
        items = fetcher._parse_atom(ET.fromstring(ATOM.encode()), "Atom")
        assert len(items) == 1
        assert items[0]["title"] == "Atom post"
        assert items[0]["url"] == "https://example.com/atom-post"
        assert items[0]["publisher"] == "Bob"
        assert items[0]["content"] == "<p>Atom body</p>"

    def test_empty_and_invalid(self):
        fetcher = FetcherChineseIndependentBlogs()
        with patch.object(fetcher, "_http_text", return_value="<rss><channel/></rss>"):
            assert fetcher._fetch_feed("https://x/feed", "x", timeout=1) == ("empty", [])
        with patch.object(fetcher, "_http_text", return_value="<html>"):
            assert fetcher._fetch_feed("https://x/feed", "x", timeout=1) == ("invalid", [])

    def test_transport_failure_isolated(self):
        fetcher = FetcherChineseIndependentBlogs()
        with patch.object(fetcher, "_http_text", side_effect=httpx.ReadTimeout("slow")):
            assert fetcher._fetch_feed("https://x/feed", "x", timeout=1) == ("failed", [])

    def test_size_limit(self):
        fetcher = FetcherChineseIndependentBlogs()
        class FakeResponse:
            encoding = "utf-8"
            def __enter__(self): return self
            def __exit__(self, *args): return False
            def raise_for_status(self): pass
            def iter_bytes(self):
                yield b"x" * (5 * 1024 * 1024 + 1)
        with patch("moni.fetch.sources.chinese_independent_blogs.httpx.stream",
                   return_value=FakeResponse()):
            with pytest.raises(ValueError, match="exceeds"):
                fetcher._http_text("https://x", 1)

    def test_html_content_is_cleaned_by_base_pipeline(self, seeded_db):
        fetcher = FetcherChineseIndependentBlogs()
        raw = [{
            "title": "HTML post",
            "url": "https://example.com/html",
            "content": "<p>Hello <strong>world</strong></p>",
            "publisher": "Feed",
        }]
        with patch.object(fetcher, "fetch", return_value=raw):
            result = fetcher.run(dry_run=True)
        assert result["parsed"] == 1
        assert result["cleaned"] == 1


class TestRotationAndBudget:
    def test_rotation_uses_persisted_cursor(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
        fetcher = FetcherChineseIndependentBlogs()
        feeds = [("A", "https://a/feed"), ("B", "https://b/feed"), ("C", "https://c/feed")]
        seen = []
        def fake_fetch(url, title, *, timeout):
            seen.append(title)
            return "ok", [{"title": title, "url": f"{url}/post", "content": ""}]
        with patch.object(fetcher, "_resolve_opml_urls", return_value=["https://opml"]), \
             patch.object(fetcher, "_load_opml", return_value=feeds), \
             patch.object(fetcher, "_load_config", return_value={
                 "max_feeds": 2, "max_articles_per_feed": 1,
                 "max_articles_total": 10, "request_delay": 0,
                 "http_timeout": 1, "consecutive_failure_limit": 3,
             }), \
             patch.object(fetcher, "_fetch_feed", side_effect=fake_fetch):
            first = fetcher.fetch()
            second = fetcher.fetch()
        assert seen == ["A", "B", "C", "A"]
        assert [x["title"] for x in first] == ["A", "B"]
        assert [x["title"] for x in second] == ["C", "A"]

    def test_article_caps(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
        fetcher = FetcherChineseIndependentBlogs()
        feeds = [("A", "https://a/feed"), ("B", "https://b/feed")]
        def fake_fetch(url, title, *, timeout):
            return "ok", [
                {"title": f"{title}{i}", "url": f"{url}/{i}", "content": ""}
                for i in range(5)
            ]
        with patch.object(fetcher, "_resolve_opml_urls", return_value=["https://opml"]), \
             patch.object(fetcher, "_load_opml", return_value=feeds), \
             patch.object(fetcher, "_load_config", return_value={
                 "max_feeds": 10, "max_articles_per_feed": 2,
                 "max_articles_total": 3, "request_delay": 0,
                 "http_timeout": 1, "consecutive_failure_limit": 3,
             }), \
             patch.object(fetcher, "_fetch_feed", side_effect=fake_fetch):
            articles = fetcher.fetch()
        assert [x["title"] for x in articles] == ["A0", "A1", "B0"]

    def test_consecutive_failures_stop_run(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
        fetcher = FetcherChineseIndependentBlogs()
        feeds = [(letter, f"https://{letter.lower()}/feed") for letter in "ABCDE"]
        calls = []
        def fail(url, title, *, timeout):
            calls.append(title)
            return "failed", []
        with patch.object(fetcher, "_resolve_opml_urls", return_value=["https://opml"]), \
             patch.object(fetcher, "_load_opml", return_value=feeds), \
             patch.object(fetcher, "_load_config", return_value={
                 "max_feeds": 10, "max_articles_per_feed": 1,
                 "max_articles_total": 10, "request_delay": 0,
                 "http_timeout": 1, "consecutive_failure_limit": 3,
             }), \
             patch.object(fetcher, "_fetch_feed", side_effect=fail):
            assert fetcher.fetch() == []
        assert calls == ["A", "B", "C"]

    def test_stats_are_returned_by_run(self, tmp_path, monkeypatch, seeded_db):
        monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
        fetcher = FetcherChineseIndependentBlogs()
        feeds = [("A", "https://a/feed"), ("B", "https://b/feed")]
        def fake_fetch(url, title, *, timeout):
            if title == "A":
                return "ok", [{"title": "A", "url": "https://a/post", "content": ""}]
            return "failed", []
        with patch.object(fetcher, "_resolve_opml_urls", return_value=["https://opml"]), \
             patch.object(fetcher, "_load_opml", return_value=feeds), \
             patch.object(fetcher, "_load_config", return_value={
                 "max_feeds": 10, "max_articles_per_feed": 1,
                 "max_articles_total": 10, "request_delay": 0,
                 "http_timeout": 1, "consecutive_failure_limit": 3,
             }), \
             patch.object(fetcher, "_fetch_feed", side_effect=fake_fetch):
            result = fetcher.run(dry_run=True)
        assert result["feeds_ok"] == 1
        assert result["feeds_failed"] == 1
        assert result["feeds_scanned"] == 2
        assert result["cursor_next"] == 0


class TestRegistrationAndSeed:
    def test_registered(self):
        from moni.fetch.base import list_fetchers
        assert list_fetchers()["chinese_independent_blogs"] is FetcherChineseIndependentBlogs

    def test_parse_rejects_non_http(self):
        fetcher = FetcherChineseIndependentBlogs()
        assert fetcher.parse({"url": "file:///etc/passwd"}) is None
        assert fetcher.parse({"url": "javascript:alert(1)"}) is None
        parsed = fetcher.parse({"title": "x", "url": "https://x", "content": "c"})
        assert parsed["source"] == "chinese_independent_blogs"

    def test_seed_defines_source_and_tag(self):
        from moni.db import seed_config
        src = next(s for s in seed_config._DEFAULT_SOURCES
                   if s["name"] == "chinese_independent_blogs")
        assert src["id"] == "src-chinese-independent-blogs"
        assert src["fetch_interval_min"] == 180
        cfg = json.loads(src["fetch_config"])
        assert cfg["max_feeds"] == 80
        assert ("src-chinese-independent-blogs", "independent-blog") in seed_config._DEFAULT_SOURCE_TAGS

    def test_fetch_config_default_exposed(self):
        from moni.web.routes.config_sources import _FETCH_CONFIG_DEFAULTS
        assert "chinese_independent_blogs" in _FETCH_CONFIG_DEFAULTS
