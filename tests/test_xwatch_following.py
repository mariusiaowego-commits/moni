"""Tests for moni.fetch.xwatch_following — pure helpers (no live Chrome)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from moni.fetch.xwatch_following import (
    following_tweet_to_synd_dict,
    scrape_following,
)


class TestFollowingTweetToSynd:
    def test_maps_fields(self):
        d = following_tweet_to_synd_dict({
            "account": "HiTw93",
            "time": "2026-08-03T23:52:00.000Z",
            "text": "hello 微信小程序",
            "url": "https://x.com/HiTw93/status/2084427092079624366",
        })
        assert d is not None
        assert d["id_str"] == "2084427092079624366"
        assert d["text"] == "hello 微信小程序"
        assert d["user"]["screen_name"] == "HiTw93"
        assert d["created_at"].startswith("2026-08-03")

    def test_missing_text_returns_none(self):
        assert following_tweet_to_synd_dict({
            "url": "https://x.com/a/status/1",
            "text": "",
        }) is None


class TestScrapeFollowingMocked:
    def test_returns_empty_when_chrome_down(self):
        with patch(
            "moni.fetch.xwatch_following.ensure_x_chrome",
            return_value=False,
        ):
            assert scrape_following(ensure=True) == []

    def test_login_lost_returns_empty(self):
        """Simulate CDP session that lands on logout page."""
        mock_ws = MagicMock()
        responses = iter([
            # Page.enable, Runtime.enable, Page.navigate
            {"id": 1, "result": {}},
            {"id": 2, "result": {}},
            {"id": 3, "result": {}},
            # probe evaluate
            {
                "id": 4,
                "result": {
                    "result": {
                        "value": {
                            "url": "https://x.com/?logout=1",
                            "n": 0,
                        },
                    },
                },
            },
        ])

        def recv():
            try:
                return __import__("json").dumps(next(responses))
            except StopIteration:
                return __import__("json").dumps({"id": 99, "result": {}})

        mock_ws.recv = recv
        mock_ws.send = MagicMock()
        mock_ws.close = MagicMock()

        with patch(
            "moni.fetch.xwatch_following.ensure_x_chrome", return_value=True,
        ), patch(
            "moni.fetch.xwatch_following._port_alive", return_value=True,
        ), patch(
            "moni.fetch.xwatch_following._list_pages",
            return_value=[{
                "type": "page",
                "url": "https://x.com/home",
                "webSocketDebuggerUrl": "ws://127.0.0.1:9222/devtools/page/x",
            }],
        ), patch(
            "websocket.create_connection", return_value=mock_ws,
        ):
            out = scrape_following(max_posts=10, max_scrolls=2, ensure=False)
        assert out == []
