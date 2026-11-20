"""Tests for moni.fetch.chrome_guard and safety guardrails (v1.0.54)."""
import os
import signal
import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from moni.fetch.chrome_daemon import (
    ensure_chrome,
    is_headless_shell_binary,
    reset_daemon_backoff,
    resolve_chrome_binary,
    stop_chrome,
)
from moni.fetch.chrome_guard import (
    ChromeBackoffTracker,
    ChromeDaemonLock,
    acquire_daemon_lock,
    check_and_record_spawn,
    get_process_cmdline,
    is_our_chrome_process,
    is_sandboxed,
    release_daemon_lock,
    reset_spawn_history,
    system_chrome_allowed,
)
from moni.fetch.xwatch_following import (
    ensure_x_chrome,
    reset_x_chrome_backoff,
    stop_x_chrome,
)


@pytest.fixture(autouse=True)
def clean_guard_state(tmp_path, monkeypatch):
    """Isolate state directory and clear locks for each test."""
    state_dir = tmp_path / "chrome_state"
    state_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("MONI_CHROME_STATE_DIR", str(state_dir))
    release_daemon_lock()
    reset_daemon_backoff()
    reset_x_chrome_backoff()
    yield
    release_daemon_lock()
    reset_daemon_backoff()
    reset_x_chrome_backoff()


# ── Sandbox Detection Tests ──────────────────────────────────────────────────


class TestSandboxDetection:
    def test_clean_environment_is_not_sandboxed(self, monkeypatch):
        for var in ["CODEX_SANDBOX", "CODEX_CI", "MONI_RUNNING_IN_SANDBOX", "HERMES_SANDBOX"]:
            monkeypatch.delenv(var, raising=False)
        monkeypatch.setattr(Path, "exists", lambda self: False)
        assert is_sandboxed() is False

    @pytest.mark.parametrize("var,val", [
        ("CODEX_SANDBOX", "seatbelt"),
        ("CODEX_SANDBOX", "1"),
        ("CODEX_CI", "1"),
        ("MONI_RUNNING_IN_SANDBOX", "1"),
        ("HERMES_SANDBOX", "1"),
    ])
    def test_sandbox_env_variables(self, monkeypatch, var, val):
        for v in ["CODEX_SANDBOX", "CODEX_CI", "MONI_RUNNING_IN_SANDBOX", "HERMES_SANDBOX"]:
            monkeypatch.delenv(v, raising=False)
        monkeypatch.setenv(var, val)
        assert is_sandboxed() is True

    def test_dockerenv_file_detects_sandbox(self, monkeypatch):
        for var in ["CODEX_SANDBOX", "CODEX_CI", "MONI_RUNNING_IN_SANDBOX", "HERMES_SANDBOX"]:
            monkeypatch.delenv(var, raising=False)
        orig_exists = Path.exists

        def mock_exists(p):
            if str(p) == "/.dockerenv":
                return True
            return orig_exists(p)

        monkeypatch.setattr(Path, "exists", mock_exists)
        assert is_sandboxed() is True

    def test_system_chrome_allowed(self, monkeypatch):
        # In sandbox -> Never allowed
        monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
        monkeypatch.setenv("MONI_ALLOW_SYSTEM_CHROME", "1")
        assert system_chrome_allowed() is False

        # Outside sandbox -> Allowed by default
        monkeypatch.delenv("CODEX_SANDBOX", raising=False)
        monkeypatch.delenv("CODEX_CI", raising=False)
        monkeypatch.delenv("MONI_RUNNING_IN_SANDBOX", raising=False)
        monkeypatch.delenv("HERMES_SANDBOX", raising=False)
        monkeypatch.setattr(Path, "exists", lambda self: False)
        assert system_chrome_allowed() is True

        # Outside sandbox but MONI_ALLOW_SYSTEM_CHROME=0 -> Prohibited
        monkeypatch.setenv("MONI_ALLOW_SYSTEM_CHROME", "0")
        assert system_chrome_allowed() is False


# ── Binary Resolution Tests ──────────────────────────────────────────────────


class TestResolveChromeBinaryGuard:
    def test_headless_missing_shell_returns_none_without_fallback(self, monkeypatch):
        monkeypatch.setattr("moni.fetch.chrome_daemon.find_chrome_headless_shell", lambda: None)
        # Even outside sandbox, headless mode must NEVER fallback to system Chrome
        monkeypatch.delenv("CODEX_SANDBOX", raising=False)
        monkeypatch.delenv("MONI_RUNNING_IN_SANDBOX", raising=False)
        monkeypatch.setattr("moni.fetch.chrome_daemon.system_chrome_allowed", lambda: True)

        bin_path, is_shell = resolve_chrome_binary(headless=True)
        assert bin_path is None
        assert is_shell is False

    def test_headless_with_shell_returns_shell(self, monkeypatch):
        fake_shell = Path("/path/to/chrome-headless-shell")
        monkeypatch.setattr("moni.fetch.chrome_daemon.find_chrome_headless_shell", lambda: fake_shell)

        bin_path, is_shell = resolve_chrome_binary(headless=True)
        assert bin_path == str(fake_shell)
        assert is_shell is True

    def test_headed_in_sandbox_returns_none(self, monkeypatch):
        monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")
        bin_path, is_shell = resolve_chrome_binary(headless=False)
        assert bin_path is None
        assert is_shell is False

    def test_headed_outside_sandbox_returns_chrome_binary(self, monkeypatch):
        monkeypatch.delenv("CODEX_SANDBOX", raising=False)
        monkeypatch.delenv("CODEX_CI", raising=False)
        monkeypatch.delenv("MONI_RUNNING_IN_SANDBOX", raising=False)
        monkeypatch.delenv("HERMES_SANDBOX", raising=False)
        monkeypatch.setattr(Path, "exists", lambda self: False)

        bin_path, is_shell = resolve_chrome_binary(headless=False)
        assert bin_path is not None
        assert is_shell is False
        # v1.0.55 起该文件要在 Linux CI 上跑：Darwin 是 Google Chrome.app，
        # Linux runner 解析到 /usr/bin/google-chrome。断言"是个真实存在的可执行路径"
        # 或"已知候选名之一"，而不是钉死 macOS 的 bundle 名。
        name = Path(bin_path).name.lower()
        assert bin_path == str(Path(bin_path))
        assert (
            "google chrome" in bin_path.lower()
            or "chromium" in bin_path.lower()
            or name in {"google-chrome", "google-chrome-stable", "chromium", "chromium-browser"}
        ), f"unexpected headed binary: {bin_path}"


# ── Single-Instance Lock & Rate Limiting Tests ───────────────────────────────


class TestLockAndRateLimiting:
    def test_lock_acquire_and_release(self, tmp_path):
        lock_file = tmp_path / "test.lock"
        lock1 = ChromeDaemonLock(lock_file)
        lock2 = ChromeDaemonLock(lock_file)

        assert lock1.acquire() is True
        # Second acquire in same instance fails
        assert lock1.acquire() is False
        # Second instance fails
        assert lock2.acquire() is False

        lock1.release()
        # After release, lock2 can acquire
        assert lock2.acquire() is True
        lock2.release()

    def test_ensure_chrome_skips_spawn_when_lock_held(self, monkeypatch):
        monkeypatch.setattr("moni.fetch.chrome_daemon._chrome_is_running", lambda: False)
        # Acquire lock beforehand
        assert acquire_daemon_lock() is True

        with patch("subprocess.Popen") as mock_popen:
            res = ensure_chrome()
            assert res is False
            mock_popen.assert_not_called()

        release_daemon_lock()

    def test_ensure_chrome_releases_lock_after_success(self, monkeypatch):
        from moni.fetch import chrome_daemon as cd

        cd.reset_daemon_backoff()
        monkeypatch.setattr(cd, "_chrome_is_running", lambda: False)
        monkeypatch.setattr(cd, "resolve_chrome_binary", lambda **kw: ("/fake/chrome-headless-shell", True))
        monkeypatch.setattr(cd, "is_sandboxed", lambda: False)
        monkeypatch.setattr(cd, "_wait_for_chrome", lambda **kw: True)

        with patch("subprocess.Popen") as mock_popen:
            mock_popen.return_value.poll.return_value = None
            assert cd.ensure_chrome() is True

        # A long-lived web process must be able to respawn Chrome later.
        assert cd.acquire_daemon_lock() is True
        cd.release_daemon_lock()
        cd.reset_daemon_backoff()

    def test_hourly_spawn_rate_limit(self, tmp_path):
        state_dir = tmp_path / "rate_limit_state"
        state_dir.mkdir()

        # Spawns 1, 2, 3 should succeed
        for i in range(3):
            allowed, err = check_and_record_spawn("test_limiter", max_per_hour=3, state_dir=state_dir)
            assert allowed is True
            assert err is None

        # 4th spawn should be blocked
        allowed, err = check_and_record_spawn("test_limiter", max_per_hour=3, state_dir=state_dir)
        assert allowed is False
        assert "hourly spawn limit" in err

    def test_ensure_chrome_blocks_spawn_when_hourly_limit_exceeded(self, monkeypatch, tmp_path):
        monkeypatch.setattr("moni.fetch.chrome_daemon._chrome_is_running", lambda: False)
        # Artificially exhaust spawn limit
        for _ in range(3):
            check_and_record_spawn("chrome_daemon", max_per_hour=3)

        with patch("subprocess.Popen") as mock_popen:
            res = ensure_chrome()
            assert res is False
            mock_popen.assert_not_called()


# ── Cross-Process Backoff Persistence Tests ─────────────────────────────────


class TestBackoffDiskPersistence:
    def test_backoff_tracker_persists_across_instances(self, tmp_path):
        state_dir = tmp_path / "persist_test"
        state_dir.mkdir()

        tracker1 = ChromeBackoffTracker("shared_test", base_backoff_s=10.0, state_dir=state_dir)
        assert tracker1.consecutive_failures == 0

        tracker1.record_failure("error one")
        assert tracker1.consecutive_failures == 1
        assert tracker1.last_error == "error one"

        # Separate instance reading same state file
        tracker2 = ChromeBackoffTracker("shared_test", base_backoff_s=10.0, state_dir=state_dir)
        assert tracker2.consecutive_failures == 1
        assert tracker2.last_error == "error one"
        assert tracker2.is_in_backoff() is True

        tracker2.record_failure("error two")
        assert tracker1.consecutive_failures == 2

        tracker1.record_success()
        assert tracker2.consecutive_failures == 0
        assert tracker2.is_in_backoff() is False


# ── Process Killing Safety Tests (No pkill, Cmdline check) ──────────────────


class TestProcessKillingGuard:
    def test_is_our_chrome_process_matches(self, monkeypatch):
        target_dir = "/Users/mt16/Library/Application Support/Google/Chrome/Profile 7"
        cmdline_matching = f"/path/to/chrome --remote-debugging-port=9223 --user-data-dir={target_dir}"
        cmdline_alien = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

        monkeypatch.setattr(
            "moni.fetch.chrome_guard.get_process_cmdline",
            lambda pid: cmdline_matching if pid == 1001 else cmdline_alien,
        )

        assert is_our_chrome_process(1001, target_dir) is True
        assert is_our_chrome_process(1002, target_dir) is False

    def test_stop_chrome_skips_alien_pid_and_never_calls_pkill(self, monkeypatch):
        # Mock port has two pids: 1001 (alien), 1002 (ours)
        monkeypatch.setattr("moni.fetch.chrome_daemon._pids_on_debug_port", lambda port: [1001, 1002])

        target_dir = "/tmp/moni-profile7"
        monkeypatch.setattr(
            "moni.fetch.chrome_daemon.is_our_chrome_process",
            lambda pid, expected_user_data_dir: pid == 1002,
        )

        killed_pids = []

        def mock_kill(pid, sig):
            killed_pids.append((pid, sig))

        monkeypatch.setattr(os, "kill", mock_kill)

        with patch("subprocess.run") as mock_run:
            stopped = stop_chrome(port=9223, user_data_dir=target_dir, grace_s=0.0)
            # pkill must NEVER be called
            mock_run.assert_not_called()
            assert stopped == 1
            # Only PID 1002 was signalled
            signalled_pids = [p for p, sig in killed_pids]
            assert 1002 in signalled_pids
            assert 1001 not in signalled_pids

    def test_stop_x_chrome_skips_alien_pid(self, monkeypatch):
        monkeypatch.setattr("moni.fetch.xwatch_following._pids_on_port", lambda port: [2001, 2002])

        target_dir = "/tmp/moni-profile6"
        monkeypatch.setattr(
            "moni.fetch.xwatch_following.is_our_chrome_process",
            lambda pid, expected_user_data_dir: pid == 2002,
        )

        killed_pids = []
        monkeypatch.setattr(os, "kill", lambda pid, sig: killed_pids.append((pid, sig)))

        stopped = stop_x_chrome(port=9222, user_data_dir=target_dir, grace_s=0.0)
        assert stopped == 1
        signalled_pids = [p for p, sig in killed_pids]
        assert 2002 in signalled_pids
        assert 2001 not in signalled_pids


# ── XWatch Headed Sandbox Guard Tests ────────────────────────────────────────


class TestXWatchGuard:
    def test_ensure_x_chrome_headed_in_sandbox_never_spawns(self, monkeypatch):
        monkeypatch.setattr("moni.fetch.xwatch_following._port_alive", lambda *a, **kw: False)
        monkeypatch.setenv("CODEX_SANDBOX", "seatbelt")

        with patch("subprocess.Popen") as mock_popen:
            res = ensure_x_chrome(headed=True)
            assert res is False
            mock_popen.assert_not_called()

    @pytest.mark.parametrize("binary", [
        "/Users/me/Library/Caches/ms-playwright/chromium_headless_shell-1223/"
        "chrome-headless-shell-mac-arm64/chrome-headless-shell",
        "chrome-headless-shell",
        None,
    ])
    def test_headless_shell_binary_detection(self, binary):
        expected = bool(binary) and "chrome-headless-shell" in str(binary)
        assert is_headless_shell_binary(binary) is expected

    def test_ensure_x_chrome_rejects_headless_shell_result(self, monkeypatch):
        monkeypatch.setattr("moni.fetch.xwatch_following._port_alive", lambda *a, **kw: False)
        monkeypatch.setattr(
            "moni.fetch.xwatch_following.resolve_chrome_binary",
            lambda **kwargs: ("/tmp/chrome-headless-shell", True),
        )

        with patch("subprocess.Popen") as mock_popen:
            assert ensure_x_chrome(headed=True) is False
            mock_popen.assert_not_called()

    def test_ensure_x_chrome_rejects_headless_shell_already_listening(self, monkeypatch):
        monkeypatch.setattr("moni.fetch.xwatch_following._port_alive", lambda *a, **kw: True)
        monkeypatch.setattr(
            "moni.fetch.xwatch_following._port_owned_by_headless_shell",
            lambda *a, **kw: True,
        )

        with patch("subprocess.Popen") as mock_popen:
            assert ensure_x_chrome(headed=True) is False
            mock_popen.assert_not_called()

    def test_port_probe_fails_closed_for_unknown_listener(self, monkeypatch):
        from moni.fetch.xwatch_following import _port_owned_by_headless_shell

        monkeypatch.setattr(
            "moni.fetch.xwatch_following._pids_on_port", lambda *a, **kw: [123]
        )
        monkeypatch.setattr(
            "moni.fetch.xwatch_following.subprocess.check_output",
            lambda *a, **kw: "/usr/sbin/unknown-daemon --port 9222\n",
        )
        assert _port_owned_by_headless_shell() is True

    def test_port_probe_accepts_identified_headed_chrome(self, monkeypatch):
        from moni.fetch.xwatch_following import _port_owned_by_headless_shell

        monkeypatch.setattr(
            "moni.fetch.xwatch_following._pids_on_port", lambda *a, **kw: [123]
        )
        monkeypatch.setattr(
            "moni.fetch.xwatch_following.subprocess.check_output",
            lambda *a, **kw: (
                "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome "
                "--remote-debugging-port=9222\n"
            ),
        )
        assert _port_owned_by_headless_shell() is False
