"""moni.fetch.xwatch_following — Channel C: logged-in Following timeline (v1.0.12).

Pure CDP scrape of ``https://x.com/home?following=true`` using Chrome
**Profile 6 on port 9222** (same profile as hermes x-watch).  Does **not**
use browser-harness (that path returned 0 posts while CDP returned 56).

Why Profile 6 / 9222
--------------------
moni's general chrome_daemon uses Profile 7 / 9223 (36kr etc.).  X login
cookies live in Profile 6 — sharing would clobber hermes x-watch.  Keep
them isolated.

Config knobs (fetch_config)
---------------------------
* ``following_enabled`` (bool, default True)
* ``following_max_posts`` (int, default 100) — stop after N unique tweets
* ``following_max_scrolls`` (int, default 40) — hard cap on scroll loops
* ``following_scroll_pause`` (float, default 1.8) — seconds between scrolls
* ``following_stop_chrome`` (bool, default **True**) — quit Profile 6 after
  scrape.  Login cookies stay on disk; leaving the process running breaks
  macOS Dock (second Chrome instance fights the daily Chrome).

Usage::

    from moni.fetch.xwatch_following import scrape_following, stop_x_chrome
    tweets = scrape_following(max_posts=100, max_scrolls=40)
    # each: {account, time, text, url, id_str}
"""
from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import time
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

# Isolated from moni Profile 7 / 9223
X_CHROME_PORT = int(os.environ.get("MONI_XWATCH_CHROME_PORT", "9222"))
X_CHROME_USER_DATA = os.environ.get(
    "MONI_XWATCH_CHROME_USER_DATA",
    "/Users/mt16/Library/Application Support/Google/Chrome/Profile 6",
)
X_CHROME_BINARY = os.environ.get(
    "MONI_CHROME_BINARY",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)
X_CHROME_LOG = "/tmp/moni-xwatch-profile6.log"

FOLLOWING_URL = "https://x.com/home?following=true"

DEFAULT_MAX_POSTS = 100
DEFAULT_MAX_SCROLLS = 40
DEFAULT_SCROLL_PAUSE = 1.8

# DOM extract — edge-scroll accumulate pattern (X virtualizes articles).
_EXTRACT_JS = r"""
(() => {
  const arts = document.querySelectorAll('article');
  const seen = new Set();
  const tweets = [];
  for (const a of arts) {
    const sc = a.querySelector('[data-testid="socialContext"]');
    if (sc && (sc.textContent || '').includes('Pinned')) continue;
    let auth = '';
    for (const link of a.querySelectorAll('a[href]')) {
      const h = link.getAttribute('href') || '';
      if (/^\/[A-Za-z0-9_]{1,15}$/.test(h) && !h.includes('photo') && !h.includes('video')) {
        auth = h.slice(1);
        break;
      }
    }
    if (!auth) continue;
    let tEl = null, mLink = null;
    for (const t of a.querySelectorAll('time')) {
      const p = t.parentElement;
      if (p && p.tagName === 'A') { tEl = t; mLink = p; break; }
    }
    if (!tEl || !mLink) continue;
    let href = mLink.getAttribute('href') || '';
    if (href.startsWith('/')) href = 'https://x.com' + href;
    if (!href || seen.has(href)) continue;
    seen.add(href);
    const parts = [];
    for (const p of a.querySelectorAll('div[lang]')) {
      const txt = (p.innerText || '').trim();
      if (txt.length > 5) parts.push(txt);
    }
    const text = parts.join('\n');
    if (!text) continue;
    tweets.push({
      account: auth,
      time: tEl.dateTime || '',
      text: text,
      url: href,
    });
  }
  return tweets;
})()
"""


def _port_alive(port: int = X_CHROME_PORT) -> bool:
    try:
        with urllib.request.urlopen(
            f"http://127.0.0.1:{port}/json/version", timeout=1.5,
        ) as resp:
            return resp.status == 200
    except Exception:
        return False


def _pids_on_port(port: int = X_CHROME_PORT) -> list[int]:
    """PIDs listening on ``port`` (listen only).  Never use pkill -f."""
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


def stop_x_chrome(*, port: int = X_CHROME_PORT, grace_s: float = 2.0) -> int:
    """Gracefully stop only the Chrome that owns the X debug port (9222).

    Does **not** touch daily Chrome or moni Profile 7 (9223).
    Login cookies remain in Profile 6 user-data-dir on disk.

    Returns number of PIDs signalled.
    """
    pids = _pids_on_port(port)
    if not pids:
        logger.debug("[xwatch.following] stop_x_chrome: nothing on %d", port)
        return 0
    for pid in pids:
        try:
            os.kill(pid, signal.SIGTERM)
            logger.info("[xwatch.following] SIGTERM Chrome pid=%d (port %d)", pid, port)
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.warning("[xwatch.following] kill %d failed: %s", pid, exc)
    time.sleep(grace_s)
    # Force leftovers on that port only
    left = _pids_on_port(port)
    for pid in left:
        try:
            os.kill(pid, signal.SIGKILL)
            logger.info("[xwatch.following] SIGKILL Chrome pid=%d", pid)
        except ProcessLookupError:
            pass
        except Exception as exc:
            logger.warning("[xwatch.following] SIGKILL %d failed: %s", pid, exc)
    return len(pids)


def ensure_x_chrome(*, headed: bool = True) -> bool:
    """Ensure Profile 6 Chrome is listening on 9222.  Never headless for X.

    Idempotent if already up.  Spawns a **separate** Chrome process from the
    daily browser — always call :func:`stop_x_chrome` after scrape so macOS
    Dock is not stuck with two Chrome masters.
    """
    if _port_alive():
        return True

    Path(X_CHROME_USER_DATA).mkdir(parents=True, exist_ok=True)
    flags = [
        X_CHROME_BINARY,
        f"--remote-debugging-port={X_CHROME_PORT}",
        f"--user-data-dir={X_CHROME_USER_DATA}",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-gpu",
        "--remote-allow-origins=*",
        "--window-size=1280,800",
        # Avoid fighting the primary Chrome profile singleton when possible
        "--disable-features=ChromeWhatsNewUI",
    ]
    # X degrades headless → 0-2 posts; always headed for following.
    if not headed:
        flags.append("--headless=new")

    log_fd = os.open(X_CHROME_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
    try:
        subprocess.Popen(
            flags,
            stdout=log_fd,
            stderr=log_fd,
            start_new_session=True,
        )
    except Exception as exc:
        try:
            os.close(log_fd)
        except Exception:
            pass
        logger.error("[xwatch.following] failed to spawn Chrome: %s", exc)
        return False

    for _ in range(30):
        if _port_alive():
            logger.info(
                "[xwatch.following] Chrome up on %d (Profile 6) — "
                "will stop after scrape to protect Dock",
                X_CHROME_PORT,
            )
            return True
        time.sleep(0.5)
    logger.error(
        "[xwatch.following] Chrome did not respond on %d — see %s",
        X_CHROME_PORT, X_CHROME_LOG,
    )
    return False


def _list_pages(port: int = X_CHROME_PORT) -> list[dict]:
    with urllib.request.urlopen(
        f"http://127.0.0.1:{port}/json/list", timeout=5,
    ) as resp:
        tabs = json.loads(resp.read())
    return [t for t in tabs if t.get("type") == "page"]


def _pick_page(pages: list[dict]) -> dict | None:
    if not pages:
        return None
    for p in pages:
        u = p.get("url") or ""
        if "x.com" in u and "login" not in u and "logout" not in u:
            return p
    for p in pages:
        if "x.com" in (p.get("url") or ""):
            return p
    return pages[0]


def _tweet_id_from_url(url: str) -> str | None:
    if not url:
        return None
    # .../status/12345
    parts = urlparse(url).path.strip("/").split("/")
    for i, part in enumerate(parts):
        if part in ("status", "statuses") and i + 1 < len(parts):
            tid = parts[i + 1].split("?")[0]
            if tid.isdigit():
                return tid
    return None


def following_tweet_to_synd_dict(tweet: dict[str, Any]) -> dict[str, Any] | None:
    """Map CDP extract shape → syndication-like dict for tweet_to_article."""
    text = (tweet.get("text") or "").strip()
    url = tweet.get("url") or ""
    tid = tweet.get("id_str") or _tweet_id_from_url(url)
    if not text or not tid:
        return None
    account = (tweet.get("account") or "").lstrip("@")
    # created_at: accept ISO from time attribute; tweet_to_article normalises
    created = tweet.get("time") or tweet.get("created_at")
    return {
        "id_str": str(tid),
        "text": text,
        "user": {"screen_name": account} if account else {},
        "created_at": created,
    }


def scrape_following(
    *,
    max_posts: int = DEFAULT_MAX_POSTS,
    max_scrolls: int = DEFAULT_MAX_SCROLLS,
    scroll_pause: float = DEFAULT_SCROLL_PAUSE,
    ensure: bool = True,
    stop_chrome: bool = True,
) -> list[dict[str, Any]]:
    """Scroll Following timeline and return up to ``max_posts`` unique tweets.

    Never raises — returns ``[]`` on failure (logged).

    By default **stops Profile 6 Chrome after scrape** (``stop_chrome=True``)
    so macOS Dock is not left with a second Chrome master.  Cookies stay
    on disk; next run reuses login without keeping a zombie process.
    """
    max_posts = max(1, int(max_posts))
    max_scrolls = max(1, int(max_scrolls))
    scroll_pause = max(0.3, float(scroll_pause))

    if ensure and not ensure_x_chrome(headed=True):
        logger.warning("[xwatch.following] Chrome Profile 6 not available")
        return []

    if not _port_alive():
        logger.warning("[xwatch.following] port %d not alive", X_CHROME_PORT)
        return []

    try:
        import websocket
    except ImportError:
        logger.error(
            "[xwatch.following] websocket-client not installed — "
            "pip install websocket-client",
        )
        return []

    tweets: list[dict[str, Any]] = []
    try:
        try:
            pages = _list_pages()
        except Exception as exc:
            logger.warning("[xwatch.following] list pages failed: %s", exc)
            return []

        page = _pick_page(pages)
        if not page or not page.get("webSocketDebuggerUrl"):
            logger.warning("[xwatch.following] no CDP page target")
            return []

        ws_url = page["webSocketDebuggerUrl"]
        try:
            try:
                ws = websocket.create_connection(
                    ws_url, timeout=15, suppress_origin=True,
                )
            except TypeError:
                ws = websocket.create_connection(ws_url, timeout=15)
        except Exception as exc:
            logger.warning(
                "[xwatch.following] websocket connect failed: %s "
                "(restart Chrome with --remote-allow-origins=*)",
                exc,
            )
            return []

        msg_id = 0

        def cdp(method: str, params: dict | None = None) -> dict:
            nonlocal msg_id
            msg_id += 1
            mid = msg_id
            ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
            deadline = time.time() + 30
            while time.time() < deadline:
                raw = ws.recv()
                data = json.loads(raw)
                if data.get("id") == mid:
                    return data
            raise TimeoutError(method)

        accum: dict[str, dict[str, Any]] = {}
        try:
            cdp("Page.enable")
            cdp("Runtime.enable")
            cdp("Page.navigate", {"url": FOLLOWING_URL})
            time.sleep(6)

            # Login probe
            probe = cdp(
                "Runtime.evaluate",
                {
                    "expression": (
                        "({url: location.href, "
                        "n: document.querySelectorAll('article').length})"
                    ),
                    "returnByValue": True,
                },
            )
            val = (((probe.get("result") or {}).get("result") or {}).get("value")) or {}
            url_now = val.get("url") or ""
            if "login" in url_now or "logout" in url_now:
                logger.warning(
                    "[xwatch.following] not logged in on Profile 6 (url=%s)",
                    url_now[:80],
                )
                return []

            for i in range(max_scrolls):
                cdp(
                    "Runtime.evaluate",
                    {
                        "expression": "window.scrollBy(0, 2000)",
                        "returnByValue": True,
                    },
                )
                time.sleep(scroll_pause)
                r = cdp(
                    "Runtime.evaluate",
                    {"expression": _EXTRACT_JS, "returnByValue": True},
                )
                batch = (
                    ((r.get("result") or {}).get("result") or {}).get("value")
                ) or []
                for t in batch:
                    if not isinstance(t, dict):
                        continue
                    u = t.get("url") or ""
                    if not u or u in accum:
                        continue
                    tid = _tweet_id_from_url(u)
                    t = dict(t)
                    t["id_str"] = tid
                    accum[u] = t
                logger.debug(
                    "[xwatch.following] scroll %d batch=%d total=%d",
                    i + 1, len(batch), len(accum),
                )
                if len(accum) >= max_posts:
                    break
        except Exception as exc:
            logger.warning("[xwatch.following] scrape error: %s", exc)
        finally:
            try:
                ws.close()
            except Exception:
                pass

        tweets = list(accum.values())[:max_posts]
        logger.info(
            "[xwatch.following] done — %d tweets (max_posts=%d max_scrolls=%d)",
            len(tweets), max_posts, max_scrolls,
        )
        return tweets
    finally:
        # Always try to release Dock: Profile 6 process must not linger.
        if stop_chrome:
            try:
                n = stop_x_chrome()
                if n:
                    logger.info(
                        "[xwatch.following] stopped %d Chrome pid(s) on %d "
                        "(Dock-safe; cookies kept on disk)",
                        n, X_CHROME_PORT,
                    )
            except Exception as exc:
                logger.warning("[xwatch.following] stop_x_chrome failed: %s", exc)
