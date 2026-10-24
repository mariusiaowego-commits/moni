#!/usr/bin/env python3
"""Verify an xwatch Chrome profile is logged in, using HEADED Chrome only.

Run this OUTSIDE the Codex/Hermes sandbox.  It launches the real Google
Chrome app (not Playwright's chrome-headless-shell), opens a temporary
remote-debugging endpoint on a dedicated port, checks https://x.com/home,
and closes only the process it started.

Usage:
    /usr/local/bin/python3 scripts/verify-xwatch-profile.py
    /usr/local/bin/python3 scripts/verify-xwatch-profile.py \
        --user-data-dir "$HOME/.moni/chrome/xwatch"

Exit codes:
    0  logged in (auth_token and ct0 present, home did not redirect to login)
    1  not verified
    2  refused (sandbox / headless-shell / unexpected binary / port in use)
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import platform
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import time
import urllib.request


def real_home() -> Path:
    if platform.system() == "Darwin":
        try:
            import pwd

            return Path(pwd.getpwuid(os.getuid()).pw_dir)
        except Exception:
            pass
    return Path.home()


def default_profile() -> Path:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from moni.fetch.profile_paths import resolve_xwatch_user_data_dir

        return resolve_xwatch_user_data_dir()
    except Exception:
        return real_home() / ".moni" / "chrome" / "xwatch"


def system_chrome() -> str | None:
    override = os.environ.get("MONI_CHROME_BINARY")
    if override:
        return override
    if platform.system() == "Darwin":
        return "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return None


def is_headless_shell(path: str) -> bool:
    return "chrome-headless-shell" in path.lower()


def is_sandboxed() -> bool:
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
        from moni.fetch.chrome_guard import is_sandboxed as guard_check

        return guard_check()
    except Exception:
        pass
    return any(
        os.environ.get(k)
        for k in ("CODEX_SANDBOX", "CODEX_CI", "MONI_RUNNING_IN_SANDBOX", "HERMES_SANDBOX")
    ) or Path("/.dockerenv").exists()


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def wait_cdp(port: int, proc: subprocess.Popen, timeout_s: float = 20.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1
            ) as resp:
                if resp.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.4)
    return False


def cookie_names(cookies_db: Path) -> set[str]:
    if not cookies_db.exists():
        return set()
    try:
        conn = sqlite3.connect(f"file:{cookies_db}?mode=ro", uri=True)
        try:
            return {
                str(row[0])
                for row in conn.execute("SELECT name FROM cookies WHERE host_key LIKE '%x.com%';")
            }
        finally:
            conn.close()
    except Exception:
        return set()


def first_page(port: int) -> dict:
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=5) as resp:
        pages = json.loads(resp.read())
    for page in pages:
        if page.get("type") == "page":
            return page
    raise RuntimeError("no page target")


def navigate_and_probe(port: int, url: str) -> dict:
    """Use the websocket CDP endpoint to navigate and read location.href."""
    try:
        import websocket
    except ImportError as exc:
        raise RuntimeError("websocket-client is required; pip install websocket-client") from exc

    page = first_page(port)
    ws_url = page.get("webSocketDebuggerUrl")
    if not ws_url:
        raise RuntimeError("page has no webSocketDebuggerUrl")
    ws = websocket.create_connection(ws_url, timeout=20, suppress_origin=True)
    try:
        msg_id = 1
        ws.send(json.dumps({"id": msg_id, "method": "Page.navigate", "params": {"url": url}}))
        deadline = time.time() + 30
        while time.time() < deadline:
            data = json.loads(ws.recv())
            if data.get("id") == msg_id:
                break
        time.sleep(8)
        msg_id += 1
        ws.send(json.dumps({
            "id": msg_id,
            "method": "Runtime.evaluate",
            "params": {
                "expression": (
                    "({url: location.href, "
                    "articles: document.querySelectorAll('article').length, "
                    "title: document.title})"
                ),
                "returnByValue": True,
            },
        }))
        deadline = time.time() + 30
        while time.time() < deadline:
            data = json.loads(ws.recv())
            if data.get("id") == msg_id:
                value = (((data.get("result") or {}).get("result") or {}).get("value")) or {}
                return value if isinstance(value, dict) else {}
        raise TimeoutError("Runtime.evaluate")
    finally:
        try:
            ws.close()
        except Exception:
            pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-data-dir", type=Path, default=None)
    parser.add_argument("--port", type=int, default=9224)
    parser.add_argument("--timeout", type=float, default=20.0)
    args = parser.parse_args()

    profile = (args.user_data_dir or default_profile()).expanduser().resolve()
    binary = system_chrome()

    if is_sandboxed():
        print("REFUSED: run this script outside the Codex/Hermes sandbox.", file=sys.stderr)
        return 2
    if not binary:
        print("REFUSED: system Google Chrome not found.", file=sys.stderr)
        return 2
    if is_headless_shell(binary):
        print(f"REFUSED: headless-shell clears X cookies: {binary}", file=sys.stderr)
        return 2
    if not profile.exists():
        print(f"REFUSED: profile not found: {profile}", file=sys.stderr)
        return 2
    if port_in_use(args.port):
        print(f"REFUSED: port {args.port} already in use.", file=sys.stderr)
        return 2

    log_path = Path(tempfile.gettempdir()) / "moni-xwatch-verify-chrome.log"
    log_fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    flags = [
        binary,
        f"--remote-debugging-port={args.port}",
        f"--user-data-dir={profile}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--window-size=1280,800",
        "--remote-allow-origins=*",
    ]
    proc = subprocess.Popen(
        flags,
        stdout=log_fd,
        stderr=log_fd,
        start_new_session=True,
    )
    try:
        if not wait_cdp(args.port, proc, timeout_s=args.timeout):
            print(f"FAIL: Chrome did not expose CDP on {args.port}; see {log_path}", file=sys.stderr)
            return 1

        probe = navigate_and_probe(args.port, "https://x.com/home")
        url_now = str(probe.get("url") or "")
        articles = int(probe.get("articles") or 0)
        lower = url_now.lower()
        names = cookie_names(profile / "Default" / "Cookies")
        has_login_cookies = "auth_token" in names and "ct0" in names
        redirected_to_login = "login" in lower or "logout" in lower

        print(f"profile={profile}")
        print(f"binary={binary}")
        print(f"url={url_now}")
        print(f"articles={articles}")
        print(f"x_cookie_names={sorted(names)}")
        print(f"has_auth_token_and_ct0={has_login_cookies}")

        if redirected_to_login:
            print("FAIL: redirected to login/logout.", file=sys.stderr)
            return 1
        if not has_login_cookies:
            print("FAIL: auth_token/ct0 missing from profile Cookies.", file=sys.stderr)
            return 1
        print("PASS: xwatch profile is logged in (headed Chrome).")
        return 0
    finally:
        try:
            os.kill(proc.pid, 15)
        except Exception:
            pass
        try:
            proc.wait(timeout=8)
        except Exception:
            try:
                os.kill(proc.pid, 9)
            except Exception:
                pass
        try:
            os.close(log_fd)
        except Exception:
            pass


if __name__ == "__main__":
    sys.exit(main())
