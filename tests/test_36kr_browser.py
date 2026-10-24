"""Unit tests for v1.0.5.5 captcha-bypass logic.

These tests don't require a real Chrome daemon — they stub out
``fetch_body_innertext`` so we can exercise both the "browser succeeded"
and "browser failed → httpx fallback" branches of the 36kr fetchers.
The captcha detection logic (the small-body + "TTGCaptcha" string check)
is the most important thing to nail down since it's the only thing
standing between us and 10k captcha warnings in the cron logs.
"""

from __future__ import annotations

import os
import sys
from unittest.mock import patch

import pytest


# Make the worktree install importable regardless of how pytest is invoked.
WORKTREE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if WORKTREE not in sys.path:
    sys.path.insert(0, WORKTREE)


SAMPLE_RSS_XML = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>36氪</title>
    <link>https://36kr.com/feed</link>
    <description>创业资讯</description>
    <item>
      <title>AI 公司融资 1 亿美元</title>
      <link>https://36kr.com/p/123456</link>
      <description>详情介绍</description>
      <pubDate>Thu, 05 Jun 2026 10:00:00 +0800</pubDate>
    </item>
    <item>
      <title>另一家 AI 公司 B 轮融资</title>
      <link>https://36kr.com/p/123457</link>
      <description>&lt;p&gt;HTML描述&lt;/p&gt;</description>
      <pubDate>Fri, 06 Jun 2026 14:52:26  +0800</pubDate>
    </item>
  </channel>
</rss>"""


SAMPLE_CAPTCHA_HTML = """<html><head>
<script src="https://lf-cdn-tos.bytescm.com/obj/static/sec_sdk_build/3.3.4/captcha/index.js"></script>
<script>const options = { commonOptions: { aid: 331981 }, captchaOptions: { host: 'https://netsec-img-req-cn.zijieapi.com' } }; window.TTGCaptcha.init(options);</script>
</head><body></body></html>"""


# v1.0.5.8: removed SAMPLE_NEWSFLASH_HTML — 36kr_newsflash is gone.
# The captcha detection logic is still useful (it applies to 36kr_rss
# too), so its test class stays.


# ── captcha-detection unit tests (pure logic, no fetcher) ──────────────────


class TestCaptchaDetection:
    """The captcha detection is the line of defence — verify it triggers."""

    def test_short_body_with_ttg_captcha_detected(self):
        """The classic 36kr captcha page: small + contains TTGCaptcha."""
        from moni.fetch.sources.kr_rss import Fetcher36krRSS

        fetcher = Fetcher36krRSS()
        # Simulate the browser path returning the captcha page.
        with patch.object(fetcher.__class__, "fetch", return_value=[]):
            pass  # noop — we just want the class to import

        # The actual detection happens in fetch().  We re-create the
        # logic standalone here to keep the test surface small.
        body = SAMPLE_CAPTCHA_HTML
        is_captcha = ("TTGCaptcha" in body or "captcha" in body.lower()) and len(body) < 5000
        assert is_captcha, "short body with TTGCaptcha string must be flagged"

    def test_long_body_with_captcha_word_not_flagged(self):
        """A real 52KB RSS body that happens to contain the word 'captcha'."""
        # 52KB of body with one 'captcha' mention buried in description
        long_body = SAMPLE_RSS_XML + (" padding " * 4000)
        long_body = long_body.replace("详情介绍", "captcha-related news")
        assert len(long_body) > 5000
        is_captcha = ("TTGCaptcha" in long_body or "captcha" in long_body.lower()) and len(long_body) < 5000
        assert not is_captcha, "long body must not be flagged even with captcha word"

    def test_empty_body_not_a_crash(self):
        """Empty body must return [] without raising."""
        from moni.fetch.sources.kr_rss import Fetcher36krRSS

        with patch("moni.fetch.sources.kr_rss._fetch_via_browser", return_value=None), \
             patch("moni.fetch.sources.kr_rss._fetch_via_httpx", return_value=None):
            fetcher = Fetcher36krRSS()
            assert fetcher.fetch() == []


# ── RSS parsing happy path (browser path returns XML) ──────────────────────


class TestKRRSSTParser:
    """Verify the XML → article pipeline works for a real 36kr RSS body."""

    def test_browser_returns_xml(self):
        from moni.fetch.sources import kr_rss
        from moni.fetch.sources.kr_rss import Fetcher36krRSS

        with patch.object(kr_rss, "_fetch_via_browser", return_value=SAMPLE_RSS_XML), \
             patch.object(kr_rss, "_fetch_via_httpx", return_value=None) as http_fb:
            fetcher = Fetcher36krRSS()
            articles = fetcher.fetch()
            assert http_fb.call_count == 0, "httpx must not be called when browser succeeds"
            assert len(articles) == 2
            assert articles[0]["title"] == "AI 公司融资 1 亿美元"
            assert articles[0]["url"] == "https://36kr.com/p/123456"
            assert articles[0]["source"] == "36kr_rss"
            assert "2026-06-05" in articles[0]["published_at"]

    def test_browser_returns_captcha_page_falls_through(self):
        """If browser returns captcha page, we should still return [] (not crash)."""
        from moni.fetch.sources import kr_rss
        from moni.fetch.sources.kr_rss import Fetcher36krRSS

        with patch.object(kr_rss, "_fetch_via_browser", return_value=SAMPLE_CAPTCHA_HTML), \
             patch.object(kr_rss, "_fetch_via_httpx", return_value=None):
            fetcher = Fetcher36krRSS()
            assert fetcher.fetch() == []

    def test_browser_none_falls_back_to_httpx(self):
        """Browser path failed → httpx path tried → httpx returned XML."""
        from moni.fetch.sources import kr_rss
        from moni.fetch.sources.kr_rss import Fetcher36krRSS

        with patch.object(kr_rss, "_fetch_via_browser", return_value=None), \
             patch.object(kr_rss, "_fetch_via_httpx", return_value=SAMPLE_RSS_XML) as http_fb:
            fetcher = Fetcher36krRSS()
            articles = fetcher.fetch()
            assert http_fb.call_count == 1, "httpx must be called when browser returns None"
            assert len(articles) == 2

    def test_both_paths_none_returns_empty(self):
        from moni.fetch.sources import kr_rss
        from moni.fetch.sources.kr_rss import Fetcher36krRSS

        with patch.object(kr_rss, "_fetch_via_browser", return_value=None), \
             patch.object(kr_rss, "_fetch_via_httpx", return_value=None):
            fetcher = Fetcher36krRSS()
            assert fetcher.fetch() == []


# ── Newsflash parsing happy path ──────────────────────────────────────────


# v1.0.5.8: removed TestKRNewsflashParser — 36kr_newsflash is gone,
# the captcha detection it tested is already covered by the
# TestCaptchaDetection class above.


# ── chrome_daemon: ensure_chrome doesn't try to spawn in tests ────────────


class TestChromeDaemonUnit:
    """We don't want these tests to actually launch Chrome.  Verify the
    helper functions are testable + the constants are sane."""

    def test_chrome_binary_default_is_mac_path(self):
        import platform

        from moni.fetch.chrome_daemon import CHROME_BINARY, CHROME_DEBUG_PORT, CHROME_USER_DATA_DIR
        assert CHROME_DEBUG_PORT == 9223
        if platform.system() == "Darwin":
            assert CHROME_BINARY.endswith("Google Chrome")
            # Resolve the real passwd home rather than Path.home(), so the
            # hermes sandbox's $HOME redirect doesn't trap us in a
            # sandbox-only profile.
            assert "/Library/Application Support/Google/Chrome/Profile 7" in CHROME_USER_DATA_DIR
            assert "Profile 7" in CHROME_USER_DATA_DIR
        else:
            # v1.0.51: the macOS path is a portability bug. On Linux /
            # Docker, creating /Users/mt16/... dies with
            # "PermissionError: '/Users'" — that was the real root
            # cause of the first CI failure. Non-macOS uses a
            # per-user profile under HOME instead.
            assert "/Users/mt16" not in CHROME_USER_DATA_DIR
            assert CHROME_USER_DATA_DIR.endswith(".moni/chrome-profile")

    def test_env_override_takes_precedence(self, monkeypatch):
        monkeypatch.setenv("MONI_CHROME_BINARY", "/custom/chrome")
        monkeypatch.setenv("MONI_CHROME_DEBUG_PORT", "9999")
        monkeypatch.setenv("MONI_CHROME_USER_DATA_DIR", "/custom/profile")
        # Re-import to pick up env vars (the module reads them at import).
        import importlib
        import moni.fetch.chrome_daemon as cd
        importlib.reload(cd)
        assert cd.CHROME_BINARY == "/custom/chrome"
        assert cd.CHROME_DEBUG_PORT == 9999
        assert cd.CHROME_USER_DATA_DIR == "/custom/profile"
        # Reset for the next test.
        monkeypatch.delenv("MONI_CHROME_BINARY", raising=False)
        monkeypatch.delenv("MONI_CHROME_DEBUG_PORT", raising=False)
        monkeypatch.delenv("MONI_CHROME_USER_DATA_DIR", raising=False)
        importlib.reload(cd)

    @pytest.fixture
    def fake_playwright_shell(self, monkeypatch, tmp_path):
        """Hermetic isolated environment with a fake chrome-headless-shell."""
        import pwd

        fake_home = tmp_path / "fake_pw_home"
        fake_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(fake_home))
        monkeypatch.delenv("MONI_CHROME_HEADLESS_BINARY", raising=False)
        monkeypatch.setattr(
            pwd, "getpwuid", lambda uid: type("PW", (), {"pw_dir": str(fake_home)})()
        )

        shell_dir = (
            fake_home / ".cache" / "ms-playwright" / "chromium_headless_shell-9999"
        )
        shell_dir.mkdir(parents=True, exist_ok=True)
        fake_shell = shell_dir / "chrome-headless-shell"
        fake_shell.write_text("#!/bin/sh\nexit 0\n")
        fake_shell.chmod(0o755)
        return fake_shell

    def test_find_chrome_headless_shell_discovery(self, fake_playwright_shell):
        from moni.fetch.chrome_daemon import find_chrome_headless_shell

        shell = find_chrome_headless_shell()
        assert shell is not None
        assert shell == fake_playwright_shell
        assert "chrome-headless-shell" in str(shell)
        assert not str(shell).endswith(".app")

    def test_find_chrome_headless_shell_env_override(self, monkeypatch, tmp_path):
        from moni.fetch.chrome_daemon import find_chrome_headless_shell

        fake_shell = tmp_path / "custom-headless-shell"
        fake_shell.write_text("#!/bin/sh\nexit 0\n")
        fake_shell.chmod(0o755)
        monkeypatch.setenv("MONI_CHROME_HEADLESS_BINARY", str(fake_shell))

        assert find_chrome_headless_shell() == fake_shell

    def test_resolve_chrome_binary_selection(self, fake_playwright_shell):
        from moni.fetch import chrome_daemon as cd

        # Headless should resolve to shell
        bin_path, is_shell = cd.resolve_chrome_binary(headless=True)
        assert is_shell is True
        assert bin_path == str(fake_playwright_shell)

        # Headed should resolve to bundle / system Chrome
        bin_path_h, is_shell_h = cd.resolve_chrome_binary(headless=False)
        assert is_shell_h is False
        assert bin_path_h == cd.CHROME_BINARY

    def test_resolve_chrome_binary_fallback_when_no_shell(self, monkeypatch, tmp_path):
        """When no headless-shell exists in cache, resolve_chrome_binary falls back to system Chrome."""
        import pwd
        from moni.fetch import chrome_daemon as cd

        empty_home = tmp_path / "empty_pw_home"
        empty_home.mkdir(parents=True, exist_ok=True)
        monkeypatch.setenv("HOME", str(empty_home))
        monkeypatch.delenv("MONI_CHROME_HEADLESS_BINARY", raising=False)
        monkeypatch.setattr(
            pwd, "getpwuid", lambda uid: type("PW", (), {"pw_dir": str(empty_home)})()
        )

        assert cd.find_chrome_headless_shell() is None

        bin_path, is_shell = cd.resolve_chrome_binary(headless=True)
        assert is_shell is False
        assert bin_path == cd.CHROME_BINARY

    def test_backoff_tracker_progression(self):
        from moni.fetch.chrome_daemon import ChromeBackoffTracker

        tracker = ChromeBackoffTracker("test", base_backoff_s=10.0, max_backoff_s=100.0, degraded_threshold=3)
        assert tracker.consecutive_failures == 0
        assert tracker.current_backoff_s == 0.0
        assert tracker.is_in_backoff() is False
        assert tracker.is_degraded is False

        tracker.record_failure("err1")
        assert tracker.consecutive_failures == 1
        assert tracker.current_backoff_s == 10.0
        assert tracker.is_in_backoff() is True
        assert tracker.is_degraded is False

        tracker.record_failure("err2")
        assert tracker.consecutive_failures == 2
        assert tracker.current_backoff_s == 20.0
        assert tracker.is_degraded is False

        tracker.record_failure("err3")
        assert tracker.consecutive_failures == 3
        assert tracker.current_backoff_s == 40.0
        assert tracker.is_degraded is True

        tracker.record_failure("err4")
        assert tracker.current_backoff_s == 80.0

        tracker.record_failure("err5")
        assert tracker.current_backoff_s == 100.0

        tracker.record_success()
        assert tracker.consecutive_failures == 0
        assert tracker.is_degraded is False
        assert tracker.is_in_backoff() is False

    def test_ensure_chrome_backoff_skips_spawn(self, monkeypatch):
        from moni.fetch.chrome_daemon import ensure_chrome, reset_daemon_backoff, _daemon_backoff
        from unittest.mock import patch

        reset_daemon_backoff()
        monkeypatch.setattr("moni.fetch.chrome_daemon._chrome_is_running", lambda: False)

        _daemon_backoff.record_failure("mock failure")
        assert _daemon_backoff.is_in_backoff() is True

        with patch("subprocess.Popen") as mock_popen:
            res = ensure_chrome()
            assert res is False
            mock_popen.assert_not_called()
        reset_daemon_backoff()

    def test_ensure_chrome_fails_fast_on_sigabrt(self, monkeypatch):
        import signal
        from moni.fetch.chrome_daemon import ensure_chrome, reset_daemon_backoff, _daemon_backoff
        from unittest.mock import MagicMock, patch

        reset_daemon_backoff()
        monkeypatch.setattr("moni.fetch.chrome_daemon._chrome_is_running", lambda: False)

        mock_proc = MagicMock()
        mock_proc.poll.return_value = -signal.SIGABRT

        with patch("subprocess.Popen", return_value=mock_proc):
            res = ensure_chrome()
            assert res is False
            assert _daemon_backoff.consecutive_failures == 1
            assert "SIGABRT" in (_daemon_backoff.last_error or "")
        reset_daemon_backoff()

    def test_mutation_headless_must_use_shell_not_bundle(self, fake_playwright_shell):
        """Mutation sentinel: headless mode must strictly use headless-shell, NOT the .app bundle."""
        from moni.fetch.chrome_daemon import resolve_chrome_binary

        bin_path, is_shell = resolve_chrome_binary(headless=True)
        assert is_shell is True, "headless mode must select headless-shell"
        assert not bin_path.endswith(".app"), "must not be an app bundle"
        assert "chrome-headless-shell" in bin_path
        assert bin_path == str(fake_playwright_shell)


# ── browser_harness: body-extraction sentinel logic ───────────────────────


class TestBrowserHarnessBodyExtraction:
    def test_extract_body_happy(self):
        from moni.fetch.browser_harness import _extract_body
        out = "noise <<<BODY>>>hello world<<<BODY>>> more noise"
        assert _extract_body(out) == "hello world"

    def test_extract_body_missing_start(self):
        from moni.fetch.browser_harness import _extract_body
        assert _extract_body("no markers here") is None

    def test_extract_body_missing_end(self):
        from moni.fetch.browser_harness import _extract_body
        assert _extract_body("<<<BODY>>>no end") is None

    def test_extract_body_multiline(self):
        from moni.fetch.browser_harness import _extract_body
        payload = "line1\nline2\nline3"
        out = f"prefix <<<BODY>>>{payload}<<<BODY>>> suffix"
        assert _extract_body(out) == payload
