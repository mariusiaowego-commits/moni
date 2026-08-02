"""Tests for moni.fetch.xwatch_discovery — DDG scrape + URL extraction.

Tests 1-5 exercise the pure ``_extract_tweet_urls`` parser against the
HTML fixtures; tests 6-8 mock httpx so we can verify the network-call
behaviour without hitting the real DDG (and so the suite stays green in
air-gapped CI).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from moni.fetch.xwatch_discovery import (
    DDG_URL,
    USER_AGENT,
    _extract_tweet_urls,
    search_tweet_urls,
)

FIX = Path(__file__).parent / "fixtures"


# ── _extract_tweet_urls (pure-HTML parser) ─────────────────────────────────


class TestExtractTweetUrls:
    def test_extracts_three_status_links_from_sample(self):
        html = (FIX / "ddg_results_sample.html").read_text(encoding="utf-8")
        urls = _extract_tweet_urls(html)
        assert len(urls) == 3
        for u in urls:
            assert u.startswith("https://x.com/")
            assert "/status/" in u

    def test_dedupes_within_page(self):
        """Sample page contains one duplicate; result set has 3 unique URLs."""
        html = (FIX / "ddg_results_sample.html").read_text(encoding="utf-8")
        urls = _extract_tweet_urls(html)
        assert len(urls) == len(set(urls))

    def test_excludes_non_status_x_links(self):
        """Sample page includes a `x.com/dev_wxapp` (no /status/) link."""
        html = (FIX / "ddg_results_sample.html").read_text(encoding="utf-8")
        urls = _extract_tweet_urls(html)
        joined = "\n".join(urls)
        assert "https://x.com/dev_wxapp\n" not in joined + "\n"
        # The profile-only URL is never present in the extracted list.
        assert "https://x.com/dev_wxapp" not in urls
        # External link should also be absent
        assert "https://example.com" not in joined

    def test_max_results_truncates(self):
        # Build a synthetic HTML with 5 distinct status URLs
        html = "\n".join(
            f'<a href="https://x.com/u{i}/status/{1000 + i}">tweet {i}</a>'
            for i in range(5)
        )
        assert len(_extract_tweet_urls(html, max_results=2)) == 2

    def test_empty_html_returns_empty(self):
        assert _extract_tweet_urls("") == []

    def test_no_tweet_urls_in_html(self):
        assert _extract_tweet_urls(
            "<html><body>no tweet links here, just lorem ipsum</body></html>"
        ) == []

    def test_lite_shape_unwraps_uddg_redirect(self):
        """DuckDuckGo Lite wraps results through //duckduckgo.com/l/?uddg=...

        The extractor must URL-decode the ``uddg`` query parameter and
        pull the target x.com status URL out from under the wrapper.
        """
        html = (FIX / "ddg_lite_results_sample.html").read_text(encoding="utf-8")
        urls = _extract_tweet_urls(html)
        # 3 distinct tweet URLs (1 literal duplicate is deduped)
        assert len(urls) == 3
        for u in urls:
            assert u.startswith("https://x.com/")
            assert "/status/" in u
        # Non-status profile and external-link should be absent.
        assert "https://x.com/dev_wxapp" not in urls  # profile only
        assert "https://example.com" not in urls


def _mock_response(status_code: int, text: str) -> httpx.Response:
    return httpx.Response(
        status_code,
        content=text.encode("utf-8"),
        request=httpx.Request("GET", DDG_URL),
    )


class TestSearchTweetUrls:
    def test_success_returns_urls(self):
        """Mocked 200 with sample HTML returns the 3 deduped URLs."""
        sample = (FIX / "ddg_results_sample.html").read_text(encoding="utf-8")
        mock_get = MagicMock(return_value=_mock_response(200, sample))

        with patch(
            "moni.fetch.xwatch_discovery.httpx.get", mock_get,
        ):
            urls = search_tweet_urls("微信小程序 触达", max_results=20)

        # 3 unique status URLs in fixture
        assert len(urls) == 3
        # Request was made to DDG_URL with site:x.com filter + User-Agent
        call = mock_get.call_args
        # Either kwargs or positional; we always pass params/headers as kwargs.
        assert call.kwargs["params"]["q"] == "site:x.com 微信小程序 触达"
        assert call.kwargs["headers"]["User-Agent"] == USER_AGENT

    def test_anomaly_retries_once_then_returns_empty(self):
        """Anomaly page → 1 retry, both anomaly → []. mock called exactly twice."""
        anomaly = (FIX / "ddg_anomaly.html").read_text(encoding="utf-8")
        mock_get = MagicMock(return_value=_mock_response(200, anomaly))

        # Patch time.sleep so the test doesn't actually pause.
        with patch(
            "moni.fetch.xwatch_discovery.httpx.get", mock_get,
        ), patch(
            "moni.fetch.xwatch_discovery.time.sleep",
        ) as mock_sleep:
            urls = search_tweet_urls("test")

        assert urls == []
        # Two GETs total — original + retry; sleep called once between them.
        assert mock_get.call_count == 2
        mock_sleep.assert_called_once_with(5)

    def test_network_exception_returns_empty(self):
        """httpx raises → []. No exception escapes."""
        mock_get = MagicMock(
            side_effect=httpx.RequestError("connection refused"),
        )

        with patch("moni.fetch.xwatch_discovery.httpx.get", mock_get):
            urls = search_tweet_urls("test")

        # Plain network errors do NOT retry; single call, []. sleep never called.
        assert urls == []
        assert mock_get.call_count == 1
