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
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

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


def _chrome_is_running() -> bool:
    """Return True if Chrome with our debug port is responding."""
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{CHROME_DEBUG_PORT}/json/version", timeout=1
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _wait_for_chrome(timeout_s: int = 15) -> bool:
    """Poll the debug port until Chrome answers or timeout elapses."""
    deadline = time.time() + timeout_s
    while time.time() < deadline:
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
        logger.debug(
            "[chrome_daemon] Chrome already responding on port %d",
            CHROME_DEBUG_PORT,
        )
        return True

    profile_path = Path(CHROME_USER_DATA_DIR)
    profile_path.mkdir(parents=True, exist_ok=True)

    flags = [
        CHROME_BINARY,
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
    try:
        subprocess.Popen(
            flags,
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,  # detach so we can exit cleanly
        )
    except FileNotFoundError:
        os.close(log_fd)
        logger.error(
            "[chrome_daemon] Chrome binary not found at %s — set MONI_CHROME_BINARY",
            CHROME_BINARY,
        )
        return False
    except Exception as exc:
        os.close(log_fd)
        logger.error("[chrome_daemon] failed to spawn Chrome: %s", exc)
        return False
    # On success, do NOT close log_fd — the daemonised Chrome process
    # inherited it.  Closing here would close the FD in this process
    # only (since start_new_session=True means Chrome is in a new
    # session, the FD table is independent), so this is actually safe
    # in practice.  We keep it open for paranoia in case start_new_session
    # is ever weakened.
    # os.close(log_fd)  # intentionally commented

    if _wait_for_chrome():
        logger.info(
            "[chrome_daemon] Chrome up on port %d (profile %s, headed=%s)",
            CHROME_DEBUG_PORT,
            CHROME_USER_DATA_DIR,
            MONI_36KR_HEADED,
        )
        return True
    logger.error(
        "[chrome_daemon] Chrome did not respond on port %d within %ds — see %s",
        CHROME_DEBUG_PORT, 15, CHROME_LOG_PATH,
    )
    return False


def stop_chrome() -> None:
    """Kill any Chrome process bound to our debug port.

    Used in tests and for manual teardown.  Production code should let
    the daemon persist between fetches for speed.
    """
    try:
        subprocess.run(
            ["pkill", "-f", f"remote-debugging-port={CHROME_DEBUG_PORT}"],
            capture_output=True,
            check=False,
        )
    except Exception as exc:
        logger.warning("[chrome_daemon] pkill failed: %s", exc)
