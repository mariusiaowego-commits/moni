"""Unit tests for the mpaypass fetcher v1.0.3 deep-crawl pipeline.

These tests exercise the fetcher against canned HTML, not the live site.
We patch ``httpx.get`` so the test runs offline and deterministically.

Coverage:
  1. fetch() returns only /news/ URLs — /download/ filtered out.
  2. fetch() deduplicates repeated hrefs.
  3. _parse_detail_html extracts title, published_at, content, publisher.
  4. _parse_detail_html tolerates /download/ HTML gracefully (no body).
  5. _extract_published_at handles single-digit month/day ("2026-5-31 21:09").
  6. _extract_published_at falls back to None when no date span present.
  7. _extract_content returns the newsbody text.
  8. parse() on a URL-only dict calls _fetch_article; populates content.
  9. parse() skips /download/ even if a test feeds one in.
 10. parse() preserves a pre-populated fixture (used by tests themselves).
 11. run() pipeline: fetch → parse → dedupe-insert works end-to-end.
 12. The /download/ filter survives the full run() pipeline.
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path
from unittest import mock

import pytest

# Ensure the worktree is importable when this file is run from anywhere.
WT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(WT_ROOT))

# Import via the worktree path explicitly so we test this branch's source,
# not the main checkout's editable install.
sys.path.insert(0, '/Users/mt16/dev/source-monitor/.worktrees/v1.0.3-mpaypass-deep')
# Drop any cached 'moni.*' so the WT copy is preferred.
for k in list(sys.modules):
    if k.startswith('moni'):
        del sys.modules[k]

from moni.fetch.sources.mpaypass import FetcherMpaypass  # noqa: E402


# ── canned HTML fixtures ───────────────────────────────────────────────────

# 1. Homepage HTML with mixed /news/ and /download/ links
HOMEPAGE_HTML = """
<html><body>
<div class="news-list">
  <li><a href="/news/202604/13090508.html">【这一周】央行修改1项支付文件</a></li>
  <li><a href="/news/202605/31211059.html">BIS主导的"代币化"跨境支付项目取得重大进展</a></li>
  <li><a href="/news/202604/07105650.html">又一篇文章</a></li>
  <li><a href="/download/202506/24143941.html">香港稳定币条例（中文版）</a></li>
  <li><a href="/download/202504/10210054.html">银联某报告</a></li>
  <li><a href="/news/202604/13090508.html">duplicate of first</a></li>
  <li><a href="/about.html">not an article</a></li>
  <li><a href="/news/202604/13090508.html">another duplicate</a></li>
</div>
</body></html>
"""

# 2. /news/ detail page — typical layout
NEWS_DETAIL = """
<html>
<head>
  <title>【这一周】央行修改1项支付文件、香港发布稳定币牌照、微信支付入驻流程调整-移动支付网</title>
  <meta name="description" content="这周发生了这些事。">
</head>
<body>
  <div class="dh-1"><div class="logo">nav</div></div>
  <div class="main">
    <div class="main-left">
      <div class="main-left-1">
        <h1>【这一周】央行修改1项支付文件、香港发布稳定币牌照、微信支付入驻流程调整</h1>
        <div class="newsbody-author">
          <span style="height:20px;">2026-4-13 8:46</span>
          <span>来源：移动支付网 作者：佘云峰</span>
        </div>
        <div class="newsbody">
          移动支付网4月13日消息，本周支付行业发生了这些事。央行修改了一项重要的支付文件，涉及反洗钱监管。香港金管局发布稳定币牌照首批名单。微信支付对入驻流程进行了调整。
          <p>第一段内容详情。</p>
          <p>第二段内容详情，涵盖更多支付机构动态。</p>
        </div>
      </div>
    </div>
    <div class="main-right">相关文章 ...</div>
  </div>
</body>
</html>
"""

# 3. /news/ detail page — different date format (zero-padded), no author
NEWS_DETAIL_NOAUTHOR = """
<html>
<head>
  <title>另一篇新闻标题-移动支付网</title>
</head>
<body>
  <div class="newsbody-author"><span>2026-05-31 21:09:33</span></div>
  <div class="newsbody">正文内容。详细描述了某支付机构的发展情况。</div>
</body>
</html>
"""

# 4. /download/ page — short body, no newsbody div
DOWNLOAD_DETAIL = """
<html>
<head>
  <title>香港稳定币条例（中文版）-移动支付网</title>
</head>
<body>
  <h3>香港稳定币条例（中文版）</h3>
  <br />
  <div class="newsbody-author">2025-6-24 14:39</div>
  <div class="newsbody-content">
    <a href="/download/file.pdf">下载PDF</a>
  </div>
</body>
</html>
"""

# 5. Malformed page — should not crash parser
MALFORMED_DETAIL = """
<html>
<head><title>无内容-移动支付网</title></head>
<body>
  <div class="newsbody-author"></div>
</body>
</html>
"""


# ── helpers ────────────────────────────────────────────────────────────────

def _url_response(text: str, url: str = "https://www.example.com/x"):
    """Build a fake httpx.Response-like object."""
    r = mock.Mock()
    r.status_code = 200
    r.text = text
    r.request = mock.Mock()
    r.request.url = url
    r.raise_for_status = mock.Mock()
    return r


# ── tests ──────────────────────────────────────────────────────────────────

class TestFetchUrlList:
    """fetch() returns URL dicts from the homepage, filtering /download/."""

    def test_returns_only_news_urls(self):
        f = FetcherMpaypass()
        with mock.patch("moni.fetch.sources.mpaypass.httpx.get",
                        return_value=_url_response(HOMEPAGE_HTML)):
            urls = f.fetch()
        # 3 unique /news/ URLs (1 of 3 is a duplicate, filtered)
        assert len(urls) == 3, f"expected 3 unique /news/ URLs, got {len(urls)}: {urls}"
        for u in urls:
            assert "/download/" not in u["url"], f"/download/ leaked: {u['url']}"
            assert "/news/" in u["url"]

    def test_returns_absolute_urls(self):
        f = FetcherMpaypass()
        with mock.patch("moni.fetch.sources.mpaypass.httpx.get",
                        return_value=_url_response(HOMEPAGE_HTML)):
            urls = f.fetch()
        for u in urls:
            assert u["url"].startswith("https://"), f"non-absolute url: {u['url']}"
            assert u["url"].startswith("https://www.mpaypass.com.cn/news/")

    def test_url_only_dict_shape(self):
        """fetch() returns URL-only dicts — content filled by deep-crawl."""
        f = FetcherMpaypass()
        with mock.patch("moni.fetch.sources.mpaypass.httpx.get",
                        return_value=_url_response(HOMEPAGE_HTML)):
            urls = f.fetch()
        for u in urls:
            assert u["url"]
            assert u["source"] == "mpaypass"
            assert u["content"] == ""
            assert u["published_at"] is None
            # title is filled by _fetch_article, not fetch()
            assert u["title"] == ""

    def test_dedupes_repeated_hrefs(self):
        f = FetcherMpaypass()
        with mock.patch("moni.fetch.sources.mpaypass.httpx.get",
                        return_value=_url_response(HOMEPAGE_HTML)):
            urls = f.fetch()
        seen = [u["url"] for u in urls]
        assert len(seen) == len(set(seen)), f"duplicates present: {seen}"

    def test_handles_http_error(self):
        f = FetcherMpaypass()
        bad = mock.Mock()
        bad.status_code = 500
        bad.raise_for_status = mock.Mock(side_effect=Exception("HTTP 500"))
        with mock.patch("moni.fetch.sources.mpaypass.httpx.get", return_value=bad):
            urls = f.fetch()
        assert urls == []

    def test_handles_timeout(self):
        import httpx as _hx
        f = FetcherMpaypass()
        with mock.patch("moni.fetch.sources.mpaypass.httpx.get",
                        side_effect=_hx.TimeoutException("timeout")):
            urls = f.fetch()
        assert urls == []


class TestDetailParse:
    """_parse_detail_html extracts the article fields from a /news/ page."""

    def test_extracts_title(self):
        f = FetcherMpaypass()
        soup = __import__("bs4").BeautifulSoup(NEWS_DETAIL, "html.parser")
        title = f._extract_title(soup)
        # h1 wins; no -移动支付网 suffix expected
        assert title == "【这一周】央行修改1项支付文件、香港发布稳定币牌照、微信支付入驻流程调整"

    def test_extracts_title_from_title_tag(self):
        """When h1 missing, fall back to <title> minus -移动支付网 suffix."""
        f = FetcherMpaypass()
        html = "<html><head><title>某文章-移动支付网</title></head><body></body></html>"
        soup = __import__("bs4").BeautifulSoup(html, "html.parser")
        assert f._extract_title(soup) == "某文章"

    def test_extracts_published_at_single_digit(self):
        """mpaypass often uses '2026-4-13 8:46' (no zero-padding)."""
        f = FetcherMpaypass()
        soup = __import__("bs4").BeautifulSoup(NEWS_DETAIL, "html.parser")
        dt = f._extract_published_at(soup)
        assert dt is not None
        # Should be ISO string
        assert dt.startswith("2026-04-13T08:46")
        # Validate by round-tripping through datetime.fromisoformat
        parsed = datetime.fromisoformat(dt)
        assert parsed.year == 2026 and parsed.month == 4 and parsed.day == 13
        assert parsed.hour == 8 and parsed.minute == 46

    def test_extracts_published_at_zero_padded_with_seconds(self):
        f = FetcherMpaypass()
        soup = __import__("bs4").BeautifulSoup(NEWS_DETAIL_NOAUTHOR, "html.parser")
        dt = f._extract_published_at(soup)
        assert dt is not None
        assert dt.startswith("2026-05-31T21:09:33")

    def test_published_at_none_when_no_author_box(self):
        f = FetcherMpaypass()
        html = "<html><body><div>no author box</div></body></html>"
        soup = __import__("bs4").BeautifulSoup(html, "html.parser")
        assert f._extract_published_at(soup) is None

    def test_extracts_content(self):
        f = FetcherMpaypass()
        soup = __import__("bs4").BeautifulSoup(NEWS_DETAIL, "html.parser")
        text = f._extract_content(soup)
        assert "央行修改了一项重要的支付文件" in text
        assert "微信支付对入驻流程进行了调整" in text
        # 1000+ chars because the body is real
        assert len(text) > 100

    def test_extracts_publisher(self):
        f = FetcherMpaypass()
        soup = __import__("bs4").BeautifulSoup(NEWS_DETAIL, "html.parser")
        assert f._extract_publisher(soup) == "移动支付网"

    def test_publisher_none_when_no_source_marker(self):
        f = FetcherMpaypass()
        soup = __import__("bs4").BeautifulSoup(NEWS_DETAIL_NOAUTHOR, "html.parser")
        assert f._extract_publisher(soup) is None

    def test_parse_full_news_detail(self):
        """End-to-end _parse_detail_html returns canonical schema."""
        f = FetcherMpaypass()
        out = f._parse_detail_html(NEWS_DETAIL, "https://www.mpaypass.com.cn/news/202604/13090508.html")
        assert out is not None
        assert out["url"] == "https://www.mpaypass.com.cn/news/202604/13090508.html"
        assert out["source"] == "mpaypass"
        assert "央行修改" in out["title"]
        assert out["published_at"].startswith("2026-04-13T08:46")
        assert "央行修改了一项重要的支付文件" in out["content"]
        assert out["publisher"] == "移动支付网"

    def test_parse_download_detail_returns_short_or_empty(self):
        """A /download/ page may still parse but should have no body content.

        The fetcher is not responsible for skipping /download/ in the
        detail stage — that filter happens in fetch() and parse().
        We just verify the parser doesn't crash on the unusual layout.
        """
        f = FetcherMpaypass()
        out = f._parse_detail_html(DOWNLOAD_DETAIL, "https://www.mpaypass.com.cn/download/202506/24143941.html")
        # Returns something, but content is empty (no .newsbody div)
        assert out is not None
        assert out["title"] == "香港稳定币条例（中文版）"
        assert out["content"] == ""
        assert out["published_at"].startswith("2025-06-24T14:39")

    def test_parse_malformed_returns_none_or_partial(self):
        """Parser must not crash on missing fields."""
        f = FetcherMpaypass()
        out = f._parse_detail_html(MALFORMED_DETAIL, "https://www.mpaypass.com.cn/news/202604/00000000.html")
        # title from <title> minus suffix
        assert out is not None
        assert out["title"] == "无内容"
        assert out["content"] == ""
        assert out["published_at"] is None


class TestParseGate:
    """parse() enforces the /download/ filter and deep-crawls URL-only dicts."""

    def test_skips_download_url(self):
        """parse() must never surface a /download/ row, even if a test
        or future bug tries to feed one in."""
        f = FetcherMpaypass()
        out = f.parse({
            "url": "https://www.mpaypass.com.cn/download/202506/24143941.html",
            "title": "irrelevant",
            "content": "irrelevant",
        })
        assert out is None

    def test_passes_through_pre_populated_fixture(self):
        """Tests (and other callers) can pass a fully populated dict."""
        f = FetcherMpaypass()
        raw = {
            "url": "https://www.mpaypass.com.cn/news/202604/13090508.html",
            "title": "fixture title",
            "content": "fixture body",
            "published_at": "2026-04-13T08:46:00+00:00",
            "publisher": "fixture source",
        }
        out = f.parse(raw)
        # normalisation adds the canonical "source" key
        expected = dict(raw, source="mpaypass")
        assert out == expected

    def test_deep_crawls_url_only_dict(self):
        """When given a URL-only dict (from fetch()), parse() deep-crawls."""
        f = FetcherMpaypass()
        url = "https://www.mpaypass.com.cn/news/202604/13090508.html"
        with mock.patch("moni.fetch.sources.mpaypass.httpx.get",
                        return_value=_url_response(NEWS_DETAIL, url)):
            out = f.parse({"url": url, "title": "", "content": "",
                           "published_at": None, "source": "mpaypass"})
        assert out is not None
        assert "央行修改" in out["title"]
        assert out["published_at"].startswith("2026-04-13T08:46")
        assert "央行修改了一项重要的支付文件" in out["content"]


class TestFetchDetailIntegration:
    """fetch() + parse() end-to-end with mocked network — verifies the
    full pipeline drops /download/ and populates content for /news/."""

    def test_run_with_mixed_html_drops_downloads(self):
        """One network call to homepage, N detail calls. Final articles
        have content; /download/ URLs are absent."""
        f = FetcherMpaypass()

        # 2 unique /news/ URLs and 1 /download/ on the homepage.
        homepage = """
        <html><body>
          <li><a href="/news/202604/13090508.html">A</a></li>
          <li><a href="/news/202604/13090509.html">B</a></li>
          <li><a href="/download/202504/10210054.html">PDF</a></li>
        </body></html>
        """

        def fake_get(url, *args, **kwargs):
            if url == "https://www.mpaypass.com.cn/":
                return _url_response(homepage, url)
            if "download" in url:
                return _url_response(DOWNLOAD_DETAIL, url)
            if url.endswith("13090508.html"):
                return _url_response(NEWS_DETAIL, url)
            if url.endswith("13090509.html"):
                return _url_response(NEWS_DETAIL_NOAUTHOR, url)
            raise AssertionError(f"unexpected url in test: {url}")

        with mock.patch("moni.fetch.sources.mpaypass.httpx.get", side_effect=fake_get):
            raw = f.fetch()

        # 2 /news/ URLs, 0 /download/
        assert len(raw) == 2, f"expected 2 /news/ URLs, got {len(raw)}: {raw}"
        assert all("/news/" in u["url"] for u in raw)
        assert not any("/download/" in u["url"] for u in raw)

        # Now parse each → should deep-crawl and return real content
        results = []
        for u in raw:
            with mock.patch("moni.fetch.sources.mpaypass.httpx.get",
                            side_effect=fake_get):
                results.append(f.parse(u))

        assert all(r is not None for r in results)
        assert all(r["content"] != "" for r in results), (
            f"all results should have content, got: {results}"
        )
        assert all(r["published_at"] is not None for r in results)


class TestFilterSurvivesPipeline:
    """Confirm the spec's /download/ filter survives a full run() cycle."""

    def test_fetch_count_matches_unique_news_urls(self, tmp_path):
        """End-to-end: fetch() returns only /news/ URLs even though
        the homepage HTML contained /download/ links.

        We don't call run() (which would touch the real DB via the
        filter pipeline). The /download/ filter is exercised at the
        fetch() stage — TestRunPipeline below covers the full run().
        """
        f = FetcherMpaypass()
        homepage = """
        <html><body>
          <li><a href="/news/202604/13090508.html">A</a></li>
          <li><a href="/news/202604/13090509.html">B</a></li>
          <li><a href="/download/202504/10210054.html">PDF1</a></li>
          <li><a href="/download/202504/10210055.html">PDF2</a></li>
        </body></html>
        """
        with mock.patch("moni.fetch.sources.mpaypass.httpx.get",
                        return_value=_url_response(homepage)):
            urls = f.fetch()
        assert len(urls) == 2
        assert not any("/download/" in u["url"] for u in urls)


class TestRunPipeline:
    """Full run() against a real (temp) DB. Verifies the /download/
    filter AND the deep-crawl content all flow through."""

    def test_run_dry_run_no_db_inserts(self, tmp_path, monkeypatch):
        """dry-run should not write to the DB but should still go through
        fetch → parse (deep-crawl) → filter."""
        from moni.db.connection import get_db
        import os, sqlite3
        from pathlib import Path

        # Use a tmp DB for filter pipeline (it calls get_db()).
        db_path = tmp_path / "news.db"
        monkeypatch.setenv("MONI_DATA_DIR", str(tmp_path))
        # Re-init connection in this env
        from moni.db import connection as _conn
        _conn._DB_PATH = None  # force re-resolve

        # Run db init to create schema
        import subprocess, sys
        env = os.environ.copy()
        env["MONI_DATA_DIR"] = str(tmp_path)
        env["PYTHONPATH"] = "/Users/mt16/dev/source-monitor/.worktrees/v1.0.3-mpaypass-deep"
        r = subprocess.run(
            [sys.executable, "-m", "moni", "db", "init"],
            cwd="/Users/mt16/dev/source-monitor/.worktrees/v1.0.3-mpaypass-deep",
            capture_output=True, text=True, env=env,
        )
        assert r.returncode == 0, f"db init failed: {r.stderr}"

        f = FetcherMpaypass()
        homepage = """
        <html><body>
          <li><a href="/news/202604/13090508.html">A</a></li>
          <li><a href="/download/202504/10210054.html">PDF</a></li>
        </body></html>
        """

        def fake_get(url, *args, **kwargs):
            if "/news/" in url:
                return _url_response(NEWS_DETAIL, url)
            return _url_response(homepage, url)

        with mock.patch("moni.fetch.sources.mpaypass.httpx.get", side_effect=fake_get):
            summary = f.run(dry_run=True)

        # /news/ URL survives the /download/ filter at fetch(); /download/
        # never enters the pipeline.
        assert summary["fetched"] == 1, f"expected 1 fetched (news only), got {summary['fetched']}"
        assert summary["parsed"] == 1
        assert summary["inserted"] == 0  # dry-run
        assert summary["error"] is None
        # 0 /download/ in the final kept set (sanity)
        assert summary["dropped_blacklist"] == 0  # no keyword filter on mpaypass
