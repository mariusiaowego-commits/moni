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
    _DEFAULTS,
    _inside_config_root,
    _is_http_url,
    _parse_date,
    _validate_config,
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

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("12", 12),
            (12.9, 12),
            (0, 1),
            (-5, 1),
            (float("inf"), _DEFAULTS["max_feeds"]),
            (True, _DEFAULTS["max_feeds"]),
            (10**9, 10_000),
            ("not-a-number", _DEFAULTS["max_feeds"]),
        ],
    )
    def test_validate_config_coerces_integer_fields(self, raw, expected):
        cfg = _validate_config({"max_feeds": raw})
        assert cfg["max_feeds"] == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("0.4", 0.4),
            (-0.2, 0.0),
            (1.2, 1.0),
            (float("nan"), _DEFAULTS["max_failure_rate"]),
            (float("inf"), _DEFAULTS["max_failure_rate"]),
            (True, _DEFAULTS["max_failure_rate"]),
            (10**400, _DEFAULTS["max_failure_rate"]),
            ("not-a-number", _DEFAULTS["max_failure_rate"]),
        ],
    )
    def test_validate_config_bounds_failure_rate(self, raw, expected):
        cfg = _validate_config({"max_failure_rate": raw})
        assert cfg["max_failure_rate"] == expected

    def test_validate_config_defaults_missing_fields(self):
        cfg = _validate_config({})
        for key, value in _DEFAULTS.items():
            assert cfg[key] == value

    def test_validate_config_clamps_min_sample_to_max_feeds(self):
        cfg = _validate_config({
            "max_feeds": 5,
            "min_feeds_before_failure_rate_check": 20,
        })
        assert cfg["min_feeds_before_failure_rate_check"] == 5

    @pytest.mark.parametrize("key", ["request_delay", "http_timeout", "max_failure_rate"])
    def test_validate_config_rejects_huge_integers(self, key):
        cfg = _validate_config({key: 10**400})
        assert cfg[key] == _DEFAULTS[key]


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

    @pytest.mark.parametrize(
        "error",
        [
            httpx.TooManyRedirects("loop"),
            httpx.DecodingError("bad encoding"),
            httpx.ConnectError("dns"),
        ],
    )
    def test_all_request_errors_count_as_network_failures(self, error):
        fetcher = FetcherChineseIndependentBlogs()
        with patch.object(fetcher, "_http_text", side_effect=error):
            assert fetcher._fetch_feed("https://x/feed", "x", timeout=1) == (
                "failed",
                [],
            )

    def test_http_status_error_is_not_network_failure(self):
        fetcher = FetcherChineseIndependentBlogs()
        request = httpx.Request("GET", "https://x/feed")
        response = httpx.Response(404, request=request)
        with patch.object(
            fetcher,
            "_http_text",
            side_effect=httpx.HTTPStatusError("404", request=request, response=response),
        ):
            assert fetcher._fetch_feed("https://x/feed", "x", timeout=1) == (
                "http_error",
                [],
            )

    @pytest.mark.parametrize("status_code", [429, 500, 502, 503])
    def test_transient_http_status_is_network_failure(self, status_code):
        fetcher = FetcherChineseIndependentBlogs()
        request = httpx.Request("GET", "https://x/feed")
        response = httpx.Response(status_code, request=request)
        with patch.object(
            fetcher,
            "_http_text",
            side_effect=httpx.HTTPStatusError(
                str(status_code), request=request, response=response
            ),
        ):
            assert fetcher._fetch_feed("https://x/feed", "x", timeout=1) == (
                "failed",
                [],
            )

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
                 "http_timeout": 1,
                 "min_feeds_before_failure_rate_check": 10,
                 "max_failure_rate": 0.7,
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
                 "http_timeout": 1,
                 "min_feeds_before_failure_rate_check": 10,
                 "max_failure_rate": 0.7,
             }), \
             patch.object(fetcher, "_fetch_feed", side_effect=fake_fetch):
            articles = fetcher.fetch()
        assert [x["title"] for x in articles] == ["A0", "A1", "B0"]

    def test_isolated_failures_do_not_stop_run(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
        fetcher = FetcherChineseIndependentBlogs()
        feeds = [(letter, f"https://{letter.lower()}/feed") for letter in "ABCDE"]
        calls = []
        def mixed(url, title, *, timeout):
            calls.append(title)
            if title in ("A", "B", "C"):
                return "failed", []
            return "ok", [{"title": title, "url": f"{url}/post", "content": ""}]
        with patch.object(fetcher, "_resolve_opml_urls", return_value=["https://opml"]), \
             patch.object(fetcher, "_load_opml", return_value=feeds), \
             patch.object(fetcher, "_load_config", return_value={
                 "max_feeds": 10, "max_articles_per_feed": 1,
                 "max_articles_total": 10, "request_delay": 0,
                 "http_timeout": 1,
                 "min_feeds_before_failure_rate_check": 10,
                 "max_failure_rate": 0.7,
             }), \
             patch.object(fetcher, "_fetch_feed", side_effect=mixed):
            articles = fetcher.fetch()
        assert calls == ["A", "B", "C", "D", "E"]
        assert [x["title"] for x in articles] == ["D", "E"]

    def test_failure_rate_stops_run(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
        fetcher = FetcherChineseIndependentBlogs()
        feeds = [(letter, f"https://{letter.lower()}/feed") for letter in "ABCDEFGHIJKL"]
        calls = []
        def mostly_fail(url, title, *, timeout):
            calls.append(title)
            if title == "A":
                return "ok", [{"title": "A", "url": "https://a/post", "content": ""}]
            return "failed", []
        with patch.object(fetcher, "_resolve_opml_urls", return_value=["https://opml"]), \
             patch.object(fetcher, "_load_opml", return_value=feeds), \
             patch.object(fetcher, "_load_config", return_value={
                 "max_feeds": 10, "max_articles_per_feed": 1,
                 "max_articles_total": 10, "request_delay": 0,
                 "http_timeout": 1,
                 "min_feeds_before_failure_rate_check": 10,
                 "max_failure_rate": 0.7,
             }), \
             patch.object(fetcher, "_fetch_feed", side_effect=mostly_fail):
            articles = fetcher.fetch()
        assert calls == list("ABCDEFGHIJ")
        assert [x["title"] for x in articles] == ["A"]

    def test_failure_rate_stops_at_eight_of_ten(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
        fetcher = FetcherChineseIndependentBlogs()
        feeds = [(letter, f"https://{letter.lower()}/feed") for letter in "ABCDEFGHIJKL"]
        calls = []
        def eight_of_ten(url, title, *, timeout):
            calls.append(title)
            if title in "AB":
                return "ok", [{"title": title, "url": f"{url}/post", "content": ""}]
            return "failed", []
        with patch.object(fetcher, "_resolve_opml_urls", return_value=["https://opml"]), \
             patch.object(fetcher, "_load_opml", return_value=feeds), \
             patch.object(fetcher, "_load_config", return_value={
                 "max_feeds": 12, "max_articles_per_feed": 1,
                 "max_articles_total": 10, "request_delay": 0,
                 "http_timeout": 1,
                 "min_feeds_before_failure_rate_check": 10,
                 "max_failure_rate": 0.7,
             }), \
             patch.object(fetcher, "_fetch_feed", side_effect=eight_of_ten):
            articles = fetcher.fetch()
        assert calls == list("ABCDEFGHIJ")
        assert [x["title"] for x in articles] == ["A", "B"]

    def test_failure_rate_boundary_not_exceeded_continues(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
        fetcher = FetcherChineseIndependentBlogs()
        feeds = [(letter, f"https://{letter.lower()}/feed") for letter in "ABCDEFGHIJKL"]
        calls = []
        def seven_of_ten(url, title, *, timeout):
            calls.append(title)
            if title in "ABCDEFG":
                return "failed", []
            return "ok", [{"title": title, "url": f"{url}/post", "content": ""}]
        with patch.object(fetcher, "_resolve_opml_urls", return_value=["https://opml"]), \
             patch.object(fetcher, "_load_opml", return_value=feeds), \
             patch.object(fetcher, "_load_config", return_value={
                 "max_feeds": 12, "max_articles_per_feed": 1,
                 "max_articles_total": 10, "request_delay": 0,
                 "http_timeout": 1,
                 "min_feeds_before_failure_rate_check": 10,
                 "max_failure_rate": 0.7,
             }), \
             patch.object(fetcher, "_fetch_feed", side_effect=seven_of_ten):
            articles = fetcher.fetch()
        assert calls == list("ABCDEFGHIJKL")
        assert len(articles) == 5

    def test_http_errors_do_not_count_toward_failure_rate(self, tmp_path, monkeypatch):
        monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
        fetcher = FetcherChineseIndependentBlogs()
        feeds = [(f"F{i}", f"https://f{i}/feed") for i in range(12)]
        calls = []
        def mostly_404(url, title, *, timeout):
            calls.append(title)
            if title == "F0":
                return "ok", [{"title": "F0", "url": "https://f0/post", "content": ""}]
            return "http_error", []
        with patch.object(fetcher, "_resolve_opml_urls", return_value=["https://opml"]), \
             patch.object(fetcher, "_load_opml", return_value=feeds), \
             patch.object(fetcher, "_load_config", return_value={
                 "max_feeds": 10, "max_articles_per_feed": 1,
                 "max_articles_total": 10, "request_delay": 0,
                 "http_timeout": 1,
                 "min_feeds_before_failure_rate_check": 10,
                 "max_failure_rate": 0.7,
             }), \
             patch.object(fetcher, "_fetch_feed", side_effect=mostly_404):
            articles = fetcher.fetch()
        assert calls == [f"F{i}" for i in range(10)]
        assert [x["title"] for x in articles] == ["F0"]

    def test_invalid_and_empty_do_not_count_toward_failure_rate(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
        fetcher = FetcherChineseIndependentBlogs()
        feeds = [(f"F{i}", f"https://f{i}/feed") for i in range(12)]
        calls = []
        def mixed(url, title, *, timeout):
            calls.append(title)
            return ("invalid" if title.endswith("0") else "empty"), []
        with patch.object(fetcher, "_resolve_opml_urls", return_value=["https://opml"]), \
             patch.object(fetcher, "_load_opml", return_value=feeds), \
             patch.object(fetcher, "_load_config", return_value={
                 "max_feeds": 12, "max_articles_per_feed": 1,
                 "max_articles_total": 10, "request_delay": 0,
                 "http_timeout": 1,
                 "min_feeds_before_failure_rate_check": 10,
                 "max_failure_rate": 0.7,
             }), \
             patch.object(fetcher, "_fetch_feed", side_effect=mixed):
            assert fetcher.fetch() == []
        assert len(calls) == 12

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
                 "http_timeout": 1,
                 "min_feeds_before_failure_rate_check": 10,
                 "max_failure_rate": 0.7,
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

    def test_retired_config_key_is_hidden_and_not_written_back(self, seeded_db):
        from fastapi.testclient import TestClient
        from moni.web.server import create_app

        conn = seeded_db
        conn.execute(
            "UPDATE sources SET fetch_config = ? WHERE name = ?",
            (
                json.dumps({
                    "max_feeds": 40,
                    "consecutive_failure_limit": 3,
                }),
                "chinese_independent_blogs",
            ),
        )
        conn.commit()

        with TestClient(create_app()) as client:
            got = client.get(
                "/api/config/sources/src-chinese-independent-blogs/fetch_config"
            )
            assert got.status_code == 200
            body = got.json()["fetch_config"]
            assert body["max_feeds"] == 40
            assert "consecutive_failure_limit" not in body

            put = client.put(
                "/api/config/sources/src-chinese-independent-blogs/fetch_config",
                json=body,
            )
            assert put.status_code == 200

        stored = json.loads(
            get_db().execute(
                "SELECT fetch_config FROM sources WHERE name = ?",
                ("chinese_independent_blogs",),
            ).fetchone()["fetch_config"]
        )
        assert "consecutive_failure_limit" not in stored
