"""Tests for moni.fetch.xwatch_syndication — URL parsing + tweet fetch + normalisation."""
from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import httpx
import pytest

from moni.fetch.xwatch_syndication import (
    SYNDICATION_URL,
    fetch_tweet,
    tweet_id_from_url,
    tweet_to_article,
)

FIX = Path(__file__).parent / "fixtures"


def _mock_response(status_code: int, payload) -> httpx.Response:
    """Build an httpx.Response with JSON body (or empty body for 404)."""
    if payload is None:
        body = b""
    elif isinstance(payload, (dict, list)):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    elif isinstance(payload, str):
        body = payload.encode("utf-8")
    else:
        body = payload
    return httpx.Response(
        status_code,
        content=body,
        request=httpx.Request("GET", SYNDICATION_URL),
    )


# ── tweet_id_from_url ────────────────────────────────────────────────────


class TestTweetIdFromUrl:
    def test_xcom_status_url(self):
        assert tweet_id_from_url("https://x.com/xiaoyangX026/status/2083475663496044761") == "2083475663496044761"

    def test_twittercom_status_url(self):
        """Historical links (twitter.com) should also be accepted."""
        assert tweet_id_from_url("https://twitter.com/handle/status/1234567890") == "1234567890"

    def test_plain_digit_string(self):
        """Bare ids (e.g. `moni xwatch ingest 2083475663496044761`) are pass-through."""
        assert tweet_id_from_url("2083475663496044761") == "2083475663496044761"

    def test_non_status_url(self):
        assert tweet_id_from_url("https://example.com/foo") is None

    def test_empty_or_none(self):
        assert tweet_id_from_url("") is None
        assert tweet_id_from_url(None) is None


# ── tweet_to_article (pure normalisation) ─────────────────────────────────


class TestTweetToArticle:
    def test_plain_tweet(self):
        data = json.loads((FIX / "tweet_sample.json").read_text(encoding="utf-8"))
        art = tweet_to_article(data, source="xwatch")

        assert art is not None
        # title = first line, ≤80 chars
        assert art["title"] == data["text"].splitlines()[0]
        assert len(art["title"]) <= 80  # noqa: cap per TECH-SPEC §8
        # url built from screen_name + id_str
        assert art["url"] == f"https://x.com/{data['user']['screen_name']}/status/{data['id_str']}"
        assert art["source"] == "xwatch"
        # published_at normalised to ISO with +00:00
        assert art["published_at"] is not None
        assert art["published_at"].endswith("+00:00")
        # content = full text (counters NOT appended)
        assert art["content"] == data["text"]
        # publisher = @screen_name
        assert art["publisher"] == f"@{data['user']['screen_name']}"

    def test_reply_tweet(self):
        """Reply-style tweet (in_reply_to_*) still produces a normal article."""
        data = json.loads((FIX / "tweet_reply_sample.json").read_text(encoding="utf-8"))
        art = tweet_to_article(data)

        assert art is not None
        assert art["title"].startswith("UX很流畅的感觉")
        assert art["publisher"] == "@xiaoyangX026"
        assert art["url"] == "https://x.com/xiaoyangX026/status/2083475663496044761"
        # published_at parses
        assert art["published_at"] is not None

    def test_missing_text(self):
        """No text → None (don't ingest blank rows)."""
        assert tweet_to_article({"user": {"screen_name": "x"}, "id_str": "1"}) is None

    def test_missing_user(self):
        """No user → fallback_url is used; publisher stays None."""
        data = {
            "id_str": "999",
            "text": "hello world\nline 2",
        }
        art = tweet_to_article(
            data, source="xwatch", fallback_url="https://x.com/anon/status/999",
        )
        assert art is not None
        assert art["url"] == "https://x.com/anon/status/999"
        assert art["publisher"] is None

    def test_title_truncation(self):
        """title cap = 80 chars; longer first line gets an ellipsis suffix."""
        long_first = "a" * 200
        art = tweet_to_article({
            "id_str": "1",
            "user": {"screen_name": "x"},
            "text": long_first,
            "created_at": "Wed Oct 22 21:30:00 +0000 2026",
        })
        assert art is not None
        assert len(art["title"]) <= 80
        # 80 chars including the ellipsis we append
        assert art["title"].endswith("…")


# ── fetch_tweet (network wrapper) ────────────────────────────────────────


class TestFetchTweet:
    def test_success_returns_dict(self):
        data = json.loads((FIX / "tweet_reply_sample.json").read_text(encoding="utf-8"))
        mock_get = MagicMock(return_value=_mock_response(200, data))

        with patch(
            "moni.fetch.xwatch_syndication.httpx.get", mock_get,
        ):
            out = fetch_tweet("2083475663496044761")

        assert out is not None
        assert out["id_str"] == "2083475663496044761"
        # Verify the request shape
        call = mock_get.call_args
        assert call.args[0] == SYNDICATION_URL
        assert call.kwargs["params"]["id"] == "2083475663496044761"
        assert call.kwargs["params"]["lang"] == "zh"

    def test_404_returns_none(self):
        mock_get = MagicMock(return_value=_mock_response(404, None))

        with patch("moni.fetch.xwatch_syndication.httpx.get", mock_get):
            assert fetch_tweet("0000000000000000000") is None

    def test_429_returns_none(self):
        mock_get = MagicMock(return_value=_mock_response(429, None))

        with patch("moni.fetch.xwatch_syndication.httpx.get", mock_get):
            assert fetch_tweet("0000000000000000000") is None

    def test_network_exception_returns_none(self):
        mock_get = MagicMock(side_effect=httpx.RequestError("timeout"))

        with patch("moni.fetch.xwatch_syndication.httpx.get", mock_get):
            assert fetch_tweet("0000000000000000000") is None

    def test_empty_id_returns_none_without_calling_network(self):
        """Defensive: don't hit the network for a falsy id."""
        mock_get = MagicMock()
        with patch("moni.fetch.xwatch_syndication.httpx.get", mock_get):
            assert fetch_tweet("") is None
            assert fetch_tweet(None) is None
        mock_get.assert_not_called()
