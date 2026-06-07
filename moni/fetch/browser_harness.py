"""browser-harness subprocess wrapper for moni fetches.

v1.0.5.5: thin wrapper around the hermes ``browser-harness`` CLI
(``~/.hermes/scripts/browser-harness.py``).  The CLI is the easiest way
to drive a running Chrome via CDP without pulling in Playwright as a
dependency — it ships with hermes, is on PATH, and supports inline
Python via ``-c``.

Why subprocess and not direct WebSocket
---------------------------------------
* 36kr captcha is one-off: we don't need a long-lived WS connection.
* Spawning ``browser-harness -c`` per fetch costs ~0.2s; acceptable.
* Code stays in plain Python — no asyncio/websockets imports.

Typical use
-----------
>>> from moni.fetch.browser_harness import fetch_body_innertext
>>> html = fetch_body_innertext("https://36kr.com/feed", settle_s=3.0)

The function navigates Chrome to ``url``, waits ``settle_s`` for JS to
finish, then returns ``document.body.innerText`` (or ``textContent``).
That's enough for RSS (where the body IS the XML) and for server-
rendered newsflash pages (where innerText strips the React chrome).
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from typing import Optional

from .chrome_daemon import CHROME_DEBUG_PORT, ensure_chrome

logger = logging.getLogger(__name__)

BROWSER_HARNESS = "browser-harness"

# Cap any single browser-harness invocation.  Navigation + settle should
# finish in ~10s; 60s leaves headroom for slow JS.
DEFAULT_TIMEOUT_S = 60

# Cap total attempts per call.  First attempt may fail because the
# page hasn't finished rendering — one retry usually suffices.
DEFAULT_RETRIES = 2


def _run(script: str, timeout_s: int) -> subprocess.CompletedProcess:
    """Run a snippet of Python through ``browser-harness -c``.

    Returns the CompletedProcess (caller inspects stdout/returncode).
    ``BU_CDP_URL`` is injected so browser-harness talks to OUR daemon
    (port 9223) and not x-watch's port 9222.
    """
    env = {**os.environ, "BU_CDP_URL": f"http://127.0.0.1:{CHROME_DEBUG_PORT}"}
    return subprocess.run(
        [BROWSER_HARNESS, "-c", script],
        capture_output=True,
        text=True,
        env=env,
        timeout=timeout_s,
    )


def _nav_script(url: str, settle_s: float) -> str:
    """Build a browser-harness Python snippet that navigates + settles + reads body.

    We reuse the same tab across fetches when possible — but for
    moni's "fire and forget" pattern, opening a fresh tab is simpler
    than tracking tab IDs.  ``new_tab`` is the browser-harness builtin
    for that; it returns a tab id we ignore.

    IMPORTANT: ``document`` and ``window`` are *JavaScript* globals,
    not Python builtins.  In the browser-harness Python REPL you call
    ``js("expression")`` to evaluate JS in the page context.  Calling
    ``document.body.innerText`` directly raises ``NameError`` — the
    first symptom is a head-scratching traceback in the log with
    ``NameError: name 'document' is not defined``.
    """
    return (
        f"import time; "
        f"new_tab({url!r}); "
        f"time.sleep({settle_s}); "
        f"body = js('document.body ? document.body.innerText : \"\"'); "
        f"print('<<<BODY>>>' + (body or '') + '<<<BODY>>>')"
    )


def fetch_body_innertext(
    url: str,
    settle_s: float = 3.0,
    timeout_s: int = DEFAULT_TIMEOUT_S,
    retries: int = DEFAULT_RETRIES,
) -> Optional[str]:
    """Navigate Chrome to ``url`` and return ``document.body.innerText``.

    Returns None on failure (Chrome down, browser-harness missing,
    navigation timeout, etc.) — the caller should fall back to a
    non-browser fetcher.

    Retries are simple "try again N times" with linear backoff.  We
    don't have a fine-grained error taxonomy to make smarter choices.
    """
    if not ensure_chrome():
        logger.error("[browser_harness] Chrome daemon failed to start — skipping")
        return None

    script = _nav_script(url, settle_s)
    last_err: Optional[str] = None
    for attempt in range(1, retries + 1):
        try:
            r = _run(script, timeout_s=timeout_s)
        except subprocess.TimeoutExpired:
            last_err = f"timeout after {timeout_s}s"
            logger.warning(
                "[browser_harness] attempt %d/%d timed out for %s",
                attempt, retries, url,
            )
            time.sleep(1.0)
            continue
        except FileNotFoundError:
            logger.error(
                "[browser_harness] %s CLI not on PATH — install hermes browser-harness",
                BROWSER_HARNESS,
            )
            return None
        except Exception as exc:
            last_err = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "[browser_harness] attempt %d/%d failed for %s: %s",
                attempt, retries, url, exc,
            )
            time.sleep(1.0)
            continue

        if r.returncode != 0:
            last_err = f"exit {r.returncode}: {r.stderr.strip()[:200]}"
            logger.warning(
                "[browser_harness] attempt %d/%d exit %d for %s: %s",
                attempt, retries, r.returncode, url, r.stderr.strip()[:200],
            )
            time.sleep(1.0)
            continue

        body = _extract_body(r.stdout)
        if body is not None:
            logger.info(
                "[browser_harness] fetched %d bytes from %s on attempt %d",
                len(body), url, attempt,
            )
            return body

        # Body markers missing — page may still be loading.
        last_err = "body markers missing in output"
        logger.warning(
            "[browser_harness] attempt %d/%d: no body markers for %s (stdout %d bytes)",
            attempt, retries, url, len(r.stdout),
        )
        time.sleep(1.0)

    logger.error(
        "[browser_harness] giving up on %s after %d attempts: %s",
        url, retries, last_err,
    )
    return None


def _extract_body(stdout: str) -> Optional[str]:
    """Pull the body text out from between our sentinels.

    The sentinels are needed because browser-harness may print
    informational lines before/after our ``print(...)`` call.
    """
    start = stdout.find("<<<BODY>>>")
    if start < 0:
        return None
    start += len("<<<BODY>>>")
    end = stdout.find("<<<BODY>>>", start)
    if end < 0:
        return None
    return stdout[start:end]
