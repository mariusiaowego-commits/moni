#!/usr/bin/env python3
"""manual_captcha.py — one-shot headed warmup for a browser-gated source.

The daemon profile is resolved by ``moni.fetch.profile_paths`` (normally
``~/.moni/chrome/daemon``).  This script launches Chrome in **headed**
mode, opens the source URL, and waits for the user to complete the
captcha in the visible window.  After the user presses Enter, cookies
are persisted in the daemon profile and later headless fetches reuse them.

Usage
-----
    python3 scripts/manual_captcha.py

Press Enter once you've completed the captcha in the Chrome window.
The script then closes Chrome cleanly (the cookies survive in
the daemon profile).

After running this once you never need to run it again — the cookies
are valid for weeks.  Re-run only if 36kr starts returning captcha
pages to the headless fetch path (i.e. cookie expiry).
"""
from __future__ import annotations

import os
import subprocess
import sys
import time
import urllib.request

# Set ALL env vars BEFORE importing moni.fetch.chrome_daemon, because
# the module reads them at import time and bakes them into module-level
# constants.  Two issues we're defending against:
#
# 1. ``MONI_36KR_HEADED`` must be "1" so Chrome runs in a visible window
#    (the user needs to see the captcha to drag the slider).
os.environ.setdefault("MONI_36KR_HEADED", "1")
# Do not pin MONI_CHROME_USER_DATA_DIR here.  profile_paths resolves the
# real home and the migrated/new profile location, so a hardcoded legacy
# path would silently recreate Chrome/Profile 7 after migration.

# Make the worktree install importable.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

# Force a fresh import of chrome_daemon so it picks up the env vars we
# just set — pytest runs (or any earlier import in the same Python
# process) would otherwise see a stale, headless-cached version.
for mod in list(sys.modules):
    if mod.startswith("moni.fetch.chrome_daemon") or mod.startswith("moni.fetch.browser_harness"):
        del sys.modules[mod]

from moni.fetch.chrome_daemon import (  # noqa: E402
    CHROME_DEBUG_PORT, CHROME_LOG_PATH, CHROME_USER_DATA_DIR, ensure_chrome, stop_chrome,
)

URLS = [
    # v1.0.5.8: /newsflashes removed (36kr_newsflash is gone).  Only
    # the RSS feed URL is opened now.
    "https://36kr.com/feed",
]


def _wait_for_debug_port(port: int, timeout_s: int = 20) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/json/version", timeout=1
            ) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False


def _open_tabs(urls: list[str]) -> None:
    """Use browser-harness to open each URL in a new tab.

    With the daemon profile + a real headed Chrome, the captcha gate will load
    and the user can complete it in the visible window.
    """
    env = {**os.environ, "BU_CDP_URL": f"http://127.0.0.1:{CHROME_DEBUG_PORT}"}
    for url in urls:
        print(f"  → opening {url}")
        r = subprocess.run(
            ["browser-harness", "-c", f"new_tab({url!r}); print('opened')"],
            capture_output=True, text=True, env=env, timeout=30,
        )
        if r.returncode != 0:
            print(f"  ⚠️  browser-harness failed: {r.stderr.strip()}", file=sys.stderr)
        time.sleep(2)  # let each tab settle before the next


def main() -> int:
    print("=" * 60)
    print("moni 36kr manual captcha bypass")
    print("=" * 60)
    print()
    print(f"Profile:    {CHROME_USER_DATA_DIR}")
    print(f"Debug port: {CHROME_DEBUG_PORT}")
    print()
    if not ensure_chrome():
        print(f"✗ Chrome failed to start.  See {CHROME_LOG_PATH}")
        return 1
    if not _wait_for_debug_port(CHROME_DEBUG_PORT):
        print("✗ Chrome started but debug port never came up")
        return 1

    print("✓ Chrome running (headed).  Opening 36kr URLs in tabs...")
    _open_tabs(URLS)
    print()
    print("─" * 60)
    print("Action: complete the slide captcha in the Chrome window.")
    print("        After it passes, you should see the real 36kr homepage.")
    print("─" * 60)
    print()
    try:
        input("Press Enter once the captcha is done (Ctrl-C to abort)... ")
    except KeyboardInterrupt:
        print("\nAborted.  Chrome left running for debugging.")
        return 130

    print()
    print("✓ Captcha acknowledged.  Stopping Chrome (cookies persist in the daemon profile).")
    stop_chrome()
    print()
    print("Next steps:")
    print("  python3 -m moni fetch --source 36kr_rss        # verify RSS works")
    print()
    return 0


if __name__ == "__main__":
    sys.exit(main())
