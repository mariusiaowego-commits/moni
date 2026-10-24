"""Chrome daemon — start/stop a headless Chrome instance for moni fetches.

v1.0.5.5: introduced for the 36kr captcha bypass.  36kr.com (and any
source behind TTGCaptcha or similar fingerprint-based gates) cannot be
fetched by a vanilla ``httpx`` call — the server returns a captcha HTML
page instead of the real content.  The fix is to ask a real Chrome
process to fetch the URL on our behalf, then read ``document.body.innerText``
back over the Chrome DevTools Protocol (CDP).

Why a separate daemon (instead of spawning a Chrome per fetch)
----------------------------------------------------------------
* Spinning a fresh Chrome takes 2-3s.  The user runs ``moni fetch`` 4
  times a day; keeping the daemon alive between calls saves that.
* ``browser-harness`` (the hermes CDP client CLI) speaks CDP and wants
  a stable ``BU_CDP_URL`` endpoint to talk to.

Why Profile 7
-------------
x-watch uses Chrome Profile 6 for X.com — sharing that profile with
moni would clobber x-watch's X login cookies when moni launches
``--user-data-dir=...Profile 6``.  moni gets its own Profile 7
directory, kept under the user library so cookies persist across runs.

Why port 9223
-------------
x-watch defaults to Chrome's remote-debugging-port 9222.  moni uses
9223 so the two daemons can run side-by-side without conflict.  See
``CHROME_DEBUG_PORT`` below.

Usage
-----
>>> from moni.fetch.chrome_daemon import ensure_chrome, stop_chrome, CHROME_DEBUG_PORT
>>> ensure_chrome()                    # idempotent — no-op if already up
>>> # ... do CDP work ...
>>> stop_chrome()                      # kills the daemon (optional — for tests)

The daemon is **single-use, single-profile**: there is no notion of
multiple daemons for different profiles.  If you need that, call
``subprocess.Popen`` directly with custom flags.
"""
from __future__ import annotations

import logging
import os
import platform
import shutil
import signal
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any

from .chrome_guard import (
    ChromeBackoffTracker,
    acquire_daemon_lock,
    check_and_record_spawn,
    is_our_chrome_process,
    is_sandboxed,
    release_daemon_lock,
    reset_spawn_history,
    system_chrome_allowed,
)

logger = logging.getLogger(__name__)


# ── Configuration ──────────────────────────────────────────────────────────
# Override these via env vars if a particular machine needs a different
# binary / port.  Defaults match the conventions in docs/BROWSER_FETCHER_PATTERN.md.

def _default_chrome_binary() -> str:
    """Where Chrome lives on this platform.

    macOS keeps the app-bundle path (moni's home machine).  Everywhere
    else (Linux CI runner, Docker) we look the binary up on PATH —
    a hardcoded ``/Applications/...`` path there would just fail.
    """
    if platform.system() == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return "google-chrome"


CHROME_BINARY = os.environ.get("MONI_CHROME_BINARY", _default_chrome_binary())
CHROME_DEBUG_PORT = int(os.environ.get("MONI_CHROME_DEBUG_PORT", "9223"))
# macOS 必须硬编码真实 home；非 macOS 用 Path.home()。
# 在 macOS 上如果用 Path.home(), 当 hermes agent 在沙箱内运行时,
# $HOME 会被重定向到 /Users/mt16/.hermes/profiles/coder/home, 导致
# 生成的 Profile 7 隔离在沙箱内, 用户日常 Chrome 无法复用。
# 但硬编码 /Users 路径在 Linux CI runner 上会因无权创建 /Users 导致
# PermissionError: '/Users'。因此非 Darwin 分支统一使用 Path.home()。


def _default_chrome_user_data_dir() -> str:
    if platform.system() == "Darwin":
        try:
            import pwd

            real_home = pwd.getpwuid(os.getuid()).pw_dir
        except Exception:
            real_home = str(Path.home())
        return str(Path(real_home) / "Library" / "Application Support" / "Google" / "Chrome" / "Profile 7")
    return str(Path.home() / ".moni" / "chrome-profile")


CHROME_USER_DATA_DIR = os.environ.get(
    "MONI_CHROME_USER_DATA_DIR",
    _default_chrome_user_data_dir(),
)
# When True, Chrome runs with a visible window so the user can complete
# a captcha challenge manually.  Switch to headless once cookies land.
MONI_36KR_HEADED = os.environ.get("MONI_36KR_HEADED", "0") == "1"

# Where stderr/stdout from the daemonised Chrome is appended.  Useful
# for debugging "why did my fetch return an empty body" — tail this log.
# v1.0.51: use the platform temp dir instead of a hardcoded ``/tmp`` so
# this works on macOS / Linux / Windows alike.
CHROME_LOG_PATH = os.path.join(tempfile.gettempdir(), "moni-chrome-profile7.log")

_daemon_backoff = ChromeBackoffTracker(name="chrome_daemon")


def find_chrome_headless_shell() -> Path | None:
    """Find the newest chrome-headless-shell in Playwright's cache, or None.

    Dynamically inspects version directories (e.g. chromium_headless_shell-1223)
    and selects the highest version number.
    """
    env_bin = os.environ.get("MONI_CHROME_HEADLESS_BINARY")
    if env_bin:
        return Path(env_bin).expanduser()

    search_roots: list[Path] = []
    try:
        import pwd

        real_home = Path(pwd.getpwuid(os.getuid()).pw_dir)
        search_roots.append(real_home / "Library" / "Caches" / "ms-playwright")
        search_roots.append(real_home / ".cache" / "ms-playwright")
    except Exception:
        pass
    search_roots.extend([
        Path.home() / "Library" / "Caches" / "ms-playwright",
        Path.home() / ".cache" / "ms-playwright",
    ])
    candidates: list[tuple[int, Path]] = []
    for root in search_roots:
        if not root.exists():
            continue
        for d in root.glob("chromium_headless_shell-*"):
            name_parts = d.name.split("-")
            version = 0
            if len(name_parts) >= 2 and name_parts[1].isdigit():
                version = int(name_parts[1])
            for exe in d.rglob("chrome-headless-shell"):
                if exe.is_file() and os.access(exe, os.X_OK):
                    candidates.append((version, exe))
    if candidates:
        candidates.sort(key=lambda c: c[0], reverse=True)
        return candidates[0][1]
    return None


def resolve_chrome_binary(*, headless: bool = True) -> tuple[str | None, bool]:
    """Resolve the executable binary path and whether it is a headless shell.

    Returns:
        (binary_path, is_headless_shell)
        If no safe/allowed binary is available, returns (None, False).
    """
    if headless:
        shell = find_chrome_headless_shell()
        if shell:
            return str(shell), True
        logger.error(
            "[chrome_daemon] Playwright chrome-headless-shell not found; "
            "refusing to spawn system Chrome in headless mode."
        )
        _daemon_backoff.record_failure("Playwright chrome-headless-shell not found")
        return None, False

    # Headed mode requested
    if not system_chrome_allowed():
        logger.error(
            "[chrome_daemon] Headed system Chrome is not allowed in sandbox / "
            "MONI_ALLOW_SYSTEM_CHROME=0."
        )
        _daemon_backoff.record_failure("System Chrome not allowed in sandbox")
        return None, False

    return CHROME_BINARY, False


def get_daemon_status() -> dict[str, Any]:
    """Return status dictionary of the Chrome daemon."""
    bin_path, is_shell = resolve_chrome_binary(headless=not MONI_36KR_HEADED)
    status = _daemon_backoff.get_status()
    status.update({
        "alive": _chrome_is_running(),
        "debug_port": CHROME_DEBUG_PORT,
        "binary": bin_path,
        "is_headless_shell": is_shell,
        "headed_requested": MONI_36KR_HEADED,
    })
    return status


def reset_daemon_backoff() -> None:
    """Reset backoff counter, spawn history, and lock (e.g. for testing)."""
    _daemon_backoff.reset()
    reset_spawn_history("chrome_daemon")
    release_daemon_lock()


def _chrome_is_running() -> bool:
    """Return True if Chrome with our debug port is responding."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{CHROME_DEBUG_PORT}/json/version", timeout=1
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _wait_for_chrome(timeout_s: int = 15, proc: subprocess.Popen | None = None) -> bool:
    """Poll the debug port until Chrome answers or timeout / early exit occurs."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if proc is not None and proc.poll() is not None:
            # Process terminated prematurely (e.g. SIGABRT in sandbox)!
            return False
        if _chrome_is_running():
            return True
        time.sleep(0.5)
    return False


def ensure_chrome() -> bool:
    """Start the moni Chrome daemon if it isn't already running.

    Idempotent: safe to call from every fetch.  Returns True if Chrome
    is up (either pre-existing or just started by us), False on failure.

    The process is detached (``start_new_session=True``) so it survives
    the Python interpreter exiting — the next ``moni fetch`` reuses it.
    """
    if _chrome_is_running():
        _daemon_backoff.record_success()
        logger.debug(
            "[chrome_daemon] Chrome already responding on port %d",
            CHROME_DEBUG_PORT,
        )
        return True

    if _daemon_backoff.is_in_backoff():
        logger.warning(
            "[chrome_daemon] In backoff after %d failures (%.1fs remaining, degraded=%s). Skipping spawn.",
            _daemon_backoff.consecutive_failures,
            _daemon_backoff.remaining_backoff_s,
            _daemon_backoff.is_degraded,
        )
        return False

    if not acquire_daemon_lock():
        logger.warning(
            "[chrome_daemon] Another instance holds daemon.lock; skipping spawn."
        )
        return False

    can_spawn, limit_err = check_and_record_spawn("chrome_daemon", max_per_hour=3)
    if not can_spawn:
        release_daemon_lock()
        _daemon_backoff.record_failure(limit_err or "hourly spawn limit exceeded")
        logger.error(
            "[chrome_daemon] %s. Entering degraded state and skipping spawn.",
            limit_err,
        )
        return False

    binary_path, is_shell = resolve_chrome_binary(headless=not MONI_36KR_HEADED)
    if not binary_path:
        release_daemon_lock()
        logger.error("[chrome_daemon] Cannot spawn Chrome: no valid binary available")
        return False

    if MONI_36KR_HEADED and is_sandboxed():
        release_daemon_lock()
        err_msg = "Headed Chrome cannot launch inside sandbox (launchservicesd registration denied)"
        _daemon_backoff.record_failure(err_msg)
        logger.error("[chrome_daemon] %s. Run outside sandbox or set MONI_36KR_HEADED=0.", err_msg)
        return False

    profile_path = Path(CHROME_USER_DATA_DIR)
    profile_path.mkdir(parents=True, exist_ok=True)

    flags = [
        binary_path,
        f"--remote-debugging-port={CHROME_DEBUG_PORT}",
        f"--user-data-dir={CHROME_USER_DATA_DIR}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-gpu",
        "--remote-allow-origins=*",
    ]
    if not MONI_36KR_HEADED:
        flags.append("--headless")
        flags.append("--window-size=1280,800")

    # Open the log file and pass the FD to Popen; close on the Python
    # side immediately so we don't keep an unneeded handle, but Chrome
    # inherits the OS-level FD via Popen so writes still land in the
    # log.  Using os.fdopen around a low-level fd avoids the Python
    # file-object's close() from racing with Chrome's open() at fork.
    log_fd = os.open(CHROME_LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    proc: subprocess.Popen | None = None
    try:
        proc = subprocess.Popen(
            flags,
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,  # detach so we can exit cleanly
        )
    except FileNotFoundError:
        os.close(log_fd)
        release_daemon_lock()
        _daemon_backoff.record_failure(f"Chrome binary not found at {binary_path}")
        logger.error(
            "[chrome_daemon] Chrome binary not found at %s — set MONI_CHROME_BINARY or install playwright",
            binary_path,
        )
        return False
    except Exception as exc:
        os.close(log_fd)
        release_daemon_lock()
        _daemon_backoff.record_failure(f"failed to spawn Chrome: {exc}")
        logger.error("[chrome_daemon] failed to spawn Chrome: %s", exc)
        return False
    # On success, do NOT close log_fd — the daemonised Chrome process
    # inherited it.  Closing here would close the FD in this process
    # only (since start_new_session=True means Chrome is in a new
    # session, the FD table is independent), so this is actually safe
    # in practice.  We keep it open for paranoia in case start_new_session
    # is ever weakened.
    # os.close(log_fd)  # intentionally commented

    if _wait_for_chrome(proc=proc):
        _daemon_backoff.record_success()
        logger.info(
            "[chrome_daemon] Chrome up on port %d (profile %s, binary=%s, headed=%s)",
            CHROME_DEBUG_PORT,
            CHROME_USER_DATA_DIR,
            binary_path,
            MONI_36KR_HEADED,
        )
        return True

    release_daemon_lock()
    exit_code = proc.poll() if proc is not None else None
    if exit_code is not None:
        err_msg = f"Chrome exited prematurely with code {exit_code}"
        if exit_code in (-6, 134) or exit_code == -signal.SIGABRT:
            err_msg += " (SIGABRT: launchservicesd mach-lookup registration denied in sandbox)"
    else:
        err_msg = f"Chrome did not respond on port {CHROME_DEBUG_PORT} within 15s"

    _daemon_backoff.record_failure(err_msg)
    logger.error("[chrome_daemon] %s — see %s", err_msg, CHROME_LOG_PATH)
    return False


def _pids_on_debug_port(port: int = CHROME_DEBUG_PORT) -> list[int]:
    """PIDs listening on ``port`` (listen only)."""
    try:
        out = subprocess.check_output(
            ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-t"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (subprocess.CalledProcessError, FileNotFoundError):
        return []
    pids: list[int] = []
    for line in out.splitlines():
        line = line.strip()
        if line.isdigit():
            pids.append(int(line))
    return pids


def stop_chrome(
    *,
    port: int = CHROME_DEBUG_PORT,
    user_data_dir: str = CHROME_USER_DATA_DIR,
    grace_s: float = 1.0,
) -> int:
    """Kill any Chrome process bound to our debug port that matches our user-data-dir.

    Used in tests and for manual teardown.  Production code should let
    the daemon persist between fetches for speed.
    """
    pids = _pids_on_debug_port(port)
    if not pids:
        release_daemon_lock()
        return 0

    valid_pids: list[int] = []
    for pid in pids:
        if is_our_chrome_process(pid, expected_user_data_dir=user_data_dir):
            valid_pids.append(pid)
        else:
            logger.warning(
                "[chrome_daemon] Refusing to kill PID %d on port %d: "
                "cmdline does not match expected user-data-dir %s (not our Chrome)",
                pid, port, user_data_dir,
            )

    for pid in valid_pids:
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.warning("[chrome_daemon] kill %d failed: %s", pid, exc)

    if grace_s > 0 and valid_pids:
        time.sleep(grace_s)

    leftovers = _pids_on_debug_port(port)
    for pid in leftovers:
        if pid in valid_pids:
            try:
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            except Exception as exc:
                logger.warning("[chrome_daemon] SIGKILL %d failed: %s", pid, exc)

    release_daemon_lock()
    return len(valid_pids)
