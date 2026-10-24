"""Chrome process and sandbox safety guardrails for moni.

v1.0.54: Prevents moni from ever launching macOS system Google Chrome
(/Applications/Google Chrome.app/...) inside sandboxed environments (such as
Codex, Hermes, CI, or containers) where LaunchServices registration is blocked
by seatbelt and triggers SIGABRT. Also enforces single-instance locking,
spawn rate-limiting, process cmdline validation before termination, and
disk-backed cross-process backoff tracking.
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import platform
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Sandbox Detection ────────────────────────────────────────────────────────


def is_sandboxed() -> bool:
    """Return True if the current process is running inside a sandboxed environment.

    Detects:
      - CODEX_SANDBOX (e.g. 'seatbelt', '1')
      - CODEX_CI ('1', 'true')
      - MONI_RUNNING_IN_SANDBOX ('1', 'true')
      - HERMES_SANDBOX ('1', 'true')
      - /.dockerenv container marker
    """
    env = os.environ
    if env.get("CODEX_SANDBOX") in ("seatbelt", "1", "true", "yes"):
        return True
    if env.get("CODEX_SANDBOX"):
        return True
    if env.get("CODEX_CI") in ("1", "true", "yes"):
        return True
    if env.get("MONI_RUNNING_IN_SANDBOX") in ("1", "true", "yes"):
        return True
    if env.get("HERMES_SANDBOX") in ("1", "true", "yes"):
        return True
    if Path("/.dockerenv").exists():
        return True
    return False


def system_chrome_allowed() -> bool:
    """Return True if spawning system Chrome .app bundle is permitted.

    Never allowed in a sandboxed environment (LaunchServices registration aborts).
    Outside sandbox, allowed unless explicitly disabled via MONI_ALLOW_SYSTEM_CHROME=0.
    """
    if is_sandboxed():
        return False
    return os.environ.get("MONI_ALLOW_SYSTEM_CHROME", "1") != "0"


# ── State Directory & Locking ────────────────────────────────────────────────


def get_moni_chrome_dir() -> Path:
    """Return the directory used for moni Chrome locks and backoff state.

    Defaults to ``~/.moni/chrome``. Can be overridden via MONI_CHROME_STATE_DIR.
    """
    env_dir = os.environ.get("MONI_CHROME_STATE_DIR")
    if env_dir:
        d = Path(env_dir).expanduser()
    else:
        if platform.system() == "Darwin":
            try:
                import pwd

                real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
            except Exception:
                real_home = Path.home()
        else:
            real_home = Path.home()
        d = real_home / ".moni" / "chrome"
    try:
        d.mkdir(parents=True, exist_ok=True)
        probe = d / f".write-probe-{os.getpid()}"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError:
        # Existing but read-only directories do not raise on mkdir. Probe with
        # a real write, then fall back to a writable temp dir (sandbox/CI).
        d = Path(tempfile.gettempdir()) / f"moni-chrome-state-{os.getuid()}"
        try:
            d.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass
    return d


class ChromeDaemonLock:
    """Non-blocking single-instance lock using fcntl.flock on daemon.lock."""

    def __init__(self, lock_path: Path | None = None) -> None:
        self._fixed_lock_path = lock_path
        self._fd: int | None = None

    @property
    def lock_path(self) -> Path:
        """Resolve the lock path at use time, not import time.

        v1.0.59: tests and workers can change MONI_CHROME_STATE_DIR after this
        module is imported.  A cached path made the lock escape that isolation.
        """
        return self._fixed_lock_path or (get_moni_chrome_dir() / "daemon.lock")

    def acquire(self) -> bool:
        """Attempt to acquire exclusive non-blocking lock.

        Returns True if acquired, False if another process holds the lock or
        already acquired in this process.
        """
        if self._fd is not None:
            # Already acquired in this process
            return False
        fd: int | None = None
        try:
            self.lock_path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(str(self.lock_path), os.O_RDWR | os.O_CREAT, 0o644)
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self._fd = fd
            return True
        except (BlockingIOError, OSError):
            if fd is not None:
                try:
                    os.close(fd)
                except Exception:
                    pass
            return False

    def release(self) -> None:
        """Release the lock if held."""
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            except Exception:
                pass
            try:
                os.close(self._fd)
            except Exception:
                pass
            self._fd = None

    def is_locked(self) -> bool:
        return self._fd is not None


_daemon_lock = ChromeDaemonLock()


def acquire_daemon_lock() -> bool:
    """Acquire the global Chrome daemon single-instance lock."""
    return _daemon_lock.acquire()


def release_daemon_lock() -> None:
    """Release the global Chrome daemon single-instance lock."""
    _daemon_lock.release()


# ── Hourly Spawn Rate Limiting ──────────────────────────────────────────────


def check_and_record_spawn(
    name: str,
    max_per_hour: int = 3,
    window_s: float = 3600.0,
    state_dir: Path | None = None,
) -> tuple[bool, str | None]:
    """Check if spawning is permitted under the hourly rate limit.

    If permitted, records the current spawn timestamp and returns (True, None).
    If exceeded, returns (False, error_message).
    """
    d = state_dir or get_moni_chrome_dir()
    history_file = d / f"spawn-history-{name}.json"
    now = time.time()
    timestamps: list[float] = []

    if history_file.exists():
        try:
            with open(history_file, "r") as f:
                data = json.load(f)
                if isinstance(data, list):
                    timestamps = [float(t) for t in data if isinstance(t, (int, float))]
        except Exception:
            timestamps = []

    recent = [t for t in timestamps if (now - t) < window_s]
    if len(recent) >= max_per_hour:
        err = f"hourly spawn limit ({max_per_hour}) exceeded for {name} ({len(recent)} spawns in past hour)"
        return False, err

    recent.append(now)
    try:
        tmp_file = history_file.with_suffix(".tmp")
        with open(tmp_file, "w") as f:
            json.dump(recent, f)
        tmp_file.replace(history_file)
    except Exception as exc:
        logger.warning("[chrome_guard] Failed to save spawn history: %s", exc)

    return True, None


def reset_spawn_history(name: str, state_dir: Path | None = None) -> None:
    """Clear spawn history (used in tests)."""
    d = state_dir or get_moni_chrome_dir()
    history_file = d / f"spawn-history-{name}.json"
    if history_file.exists():
        try:
            history_file.unlink()
        except Exception:
            pass


# ── Process Cmdline Verification ────────────────────────────────────────────


def get_process_cmdline(pid: int) -> str:
    """Retrieve the full command line of a process using ps."""
    try:
        out = subprocess.check_output(
            ["ps", "-p", str(pid), "-o", "command="],
            text=True,
            stderr=subprocess.DEVNULL,
        )
        return out.strip()
    except Exception:
        return ""


def is_our_chrome_process(pid: int, expected_user_data_dir: str) -> bool:
    """Check if process cmdline contains --user-data-dir matching expected path.

    Prevents moni from accidentally killing another project's or the user's
    daily Chrome browser bound to the same port.
    """
    cmdline = get_process_cmdline(pid)
    if not cmdline:
        return False
    if f"--user-data-dir={expected_user_data_dir}" in cmdline:
        return True
    if "--user-data-dir" in cmdline and expected_user_data_dir in cmdline:
        return True
    return False


# ── Disk-Backed Cross-Process Backoff Tracker ───────────────────────────────


class ChromeBackoffTracker:
    """Tracks launch failures with exponential backoff and disk persistence.

    State is stored at ``~/.moni/chrome/backoff-<name>.json`` so backoff
    persists across separate Python processes (CLI runs, web workers).
    """

    def __init__(
        self,
        name: str,
        base_backoff_s: float = 60.0,
        max_backoff_s: float = 1800.0,  # 30 minutes
        degraded_threshold: int = 3,
        state_dir: Path | None = None,
    ) -> None:
        self.name = name
        self.base_backoff_s = float(
            os.environ.get(f"MONI_{name.upper()}_BACKOFF_BASE_S", str(base_backoff_s))
        )
        self.max_backoff_s = float(
            os.environ.get(f"MONI_{name.upper()}_BACKOFF_MAX_S", str(max_backoff_s))
        )
        self.degraded_threshold = degraded_threshold
        self._state_dir = state_dir
        # Keep the last state in memory so rate limiting/backoff still works
        # when the on-disk state directory is read-only (sandbox/CI). Disk is
        # authoritative when writable; memory is the fallback.
        self._memory_state: dict[str, Any] | None = None
        self._persistence_ok = True

    @property
    def state_file(self) -> Path:
        d = self._state_dir or get_moni_chrome_dir()
        return d / f"backoff-{self.name}.json"

    def _read_state(self) -> dict[str, Any]:
        if not self.state_file.exists():
            return {
                "consecutive_failures": 0,
                "last_failure_time": None,
                "last_error": None,
            }
        try:
            with open(self.state_file, "r") as f:
                data = json.load(f)
                return {
                    "consecutive_failures": int(data.get("consecutive_failures", 0)),
                    "last_failure_time": data.get("last_failure_time"),
                    "last_error": data.get("last_error"),
                }
        except Exception:
            return {
                "consecutive_failures": 0,
                "last_failure_time": None,
                "last_error": None,
            }

    def _write_state(self, state: dict[str, Any]) -> None:
        self._memory_state = dict(state)
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp_file = self.state_file.with_suffix(".tmp")
            with open(tmp_file, "w") as f:
                json.dump(state, f)
            tmp_file.replace(self.state_file)
            self._persistence_ok = True
        except Exception as exc:
            self._persistence_ok = False
            logger.warning("[chrome_guard] Failed to write backoff state for %s: %s", self.name, exc)

    def _state(self) -> dict[str, Any]:
        """Read disk state, falling back to the most recent in-memory state."""
        # Disk is authoritative when writes work. If the latest write failed
        # (read-only sandbox/CI), memory is the fallback so backoff still works.
        if self.state_file.exists():
            disk = self._read_state()
            if self._persistence_ok:
                return disk
            if self._memory_state is not None:
                return dict(self._memory_state)
            return disk
        if self._memory_state is not None:
            return dict(self._memory_state)
        return self._read_state()

    @property
    def consecutive_failures(self) -> int:
        return self._state()["consecutive_failures"]

    @property
    def last_failure_time(self) -> float | None:
        return self._state()["last_failure_time"]

    @property
    def last_error(self) -> str | None:
        return self._state()["last_error"]

    def record_success(self) -> None:
        self._write_state({
            "consecutive_failures": 0,
            "last_failure_time": None,
            "last_error": None,
        })

    def record_failure(self, error: str) -> None:
        current = self._state()
        new_state = {
            "consecutive_failures": current["consecutive_failures"] + 1,
            "last_failure_time": time.time(),
            "last_error": error,
        }
        self._write_state(new_state)

    def reset(self) -> None:
        self._memory_state = None
        self._persistence_ok = True
        self._write_state({
            "consecutive_failures": 0,
            "last_failure_time": None,
            "last_error": None,
        })

    @property
    def current_backoff_s(self) -> float:
        fails = self.consecutive_failures
        if fails == 0:
            return 0.0
        backoff = self.base_backoff_s * (2 ** (fails - 1))
        return min(backoff, self.max_backoff_s)

    @property
    def remaining_backoff_s(self) -> float:
        state = self._state()
        fails = state["consecutive_failures"]
        fail_time = state["last_failure_time"]
        if not fail_time or fails == 0:
            return 0.0
        elapsed = time.time() - float(fail_time)
        return max(0.0, self.current_backoff_s - elapsed)

    def is_in_backoff(self) -> bool:
        return self.remaining_backoff_s > 0

    @property
    def is_degraded(self) -> bool:
        return self.consecutive_failures >= self.degraded_threshold

    def get_status(self) -> dict[str, Any]:
        state = self._state()
        return {
            "name": self.name,
            "consecutive_failures": state["consecutive_failures"],
            "is_degraded": self.is_degraded,
            "in_backoff": self.is_in_backoff(),
            "remaining_backoff_s": round(self.remaining_backoff_s, 1),
            "current_backoff_s": round(self.current_backoff_s, 1),
            "last_error": state["last_error"],
        }
