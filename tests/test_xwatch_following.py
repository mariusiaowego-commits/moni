"""Tests for moni.fetch.xwatch_following — pure helpers (no live Chrome)."""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import moni.fetch.xwatch_following as xwatch_following
from moni.fetch.xwatch_following import following_tweet_to_synd_dict, scrape_following


def test_chrome_paths_are_portable(monkeypatch, tmp_path):
    """Chrome profile and log paths must not hardcode another machine's home."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(xwatch_following.platform, "system", lambda: "Linux")

    profile = xwatch_following._default_xwatch_chrome_user_data_dir()
    assert "/Users/mt16" not in profile
    # v1.0.58: 默认目录由 profile_paths 决定 (~/.moni/chrome/xwatch)；
    # 若本机还留着 legacy Profile 6 数据会自动认领, 两种都是合法结果。
    assert profile.endswith(".moni/chrome/xwatch") or "Google/Chrome/Profile 6" in profile

    assert xwatch_following.X_CHROME_LOG == os.path.join(
        tempfile.gettempdir(), "moni-xwatch-profile6.log"
    )

    monkeypatch.setattr(xwatch_following.platform, "system", lambda: "Darwin")
    import pwd

    darwin_profile = xwatch_following._default_xwatch_chrome_user_data_dir()
    assert (
        darwin_profile.endswith(".moni/chrome/xwatch")
        or darwin_profile
        == str(
            Path(pwd.getpwuid(os.getuid()).pw_dir)
            / "Library"
            / "Application Support"
            / "Google"
            / "Chrome"
            / "Profile 6"
        )
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


class TestEnsureXChromeFailFastAndBackoff:
    def test_headed_chrome_fails_fast_in_sandbox_env(self, monkeypatch):
        from moni.fetch.xwatch_following import (
            ensure_x_chrome,
            reset_x_chrome_backoff,
            get_x_chrome_status,
        )

        reset_x_chrome_backoff()
        monkeypatch.setattr("moni.fetch.xwatch_following._port_alive", lambda *a, **kw: False)
        monkeypatch.setenv("MONI_RUNNING_IN_SANDBOX", "1")

        with patch("subprocess.Popen") as mock_popen:
            res = ensure_x_chrome(headed=True)
            assert res is False
            mock_popen.assert_not_called()

        status = get_x_chrome_status()
        assert status["consecutive_failures"] == 1
        assert "launchservicesd" in status["last_error"]
        reset_x_chrome_backoff()

    def test_headed_chrome_early_sigabrt_triggers_backoff(self, monkeypatch):
        import signal
        from moni.fetch.xwatch_following import (
            ensure_x_chrome,
            reset_x_chrome_backoff,
            _xwatch_backoff,
        )

        reset_x_chrome_backoff()
        monkeypatch.setattr("moni.fetch.xwatch_following._port_alive", lambda *a, **kw: False)
        # v1.0.54: 沙箱内会在 spawn 之前就 fail-fast，走不到 SIGABRT 分支。
        # 这条测试验的是"spawn 后立刻 SIGABRT"的后半段逻辑，所以要显式声明非沙箱。
        monkeypatch.setattr("moni.fetch.xwatch_following.is_sandboxed", lambda: False)
        monkeypatch.setattr(
            "moni.fetch.xwatch_following.resolve_chrome_binary",
            lambda **kw: ("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome", False),
        )

        mock_proc = MagicMock()
        mock_proc.poll.return_value = -signal.SIGABRT

        with patch("subprocess.Popen", return_value=mock_proc):
            res = ensure_x_chrome(headed=True)
            assert res is False
            assert _xwatch_backoff.consecutive_failures == 1
            assert "SIGABRT" in (_xwatch_backoff.last_error or "")
            assert _xwatch_backoff.is_in_backoff() is True

        # Subsequent call should be blocked by backoff immediately without spawning
        with patch("subprocess.Popen") as mock_popen2:
            res2 = ensure_x_chrome(headed=True)
            assert res2 is False
            mock_popen2.assert_not_called()

        reset_x_chrome_backoff()

    def test_status_reflects_degraded(self):
        from moni.fetch.xwatch_following import (
            reset_x_chrome_backoff,
            get_x_chrome_status,
            _xwatch_backoff,
        )

        reset_x_chrome_backoff()
        assert get_x_chrome_status()["is_degraded"] is False

        _xwatch_backoff.record_failure("f1")
        _xwatch_backoff.record_failure("f2")
        _xwatch_backoff.record_failure("f3")
        status = get_x_chrome_status()
        assert status["is_degraded"] is True
        assert status["consecutive_failures"] == 3
        reset_x_chrome_backoff()
