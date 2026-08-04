"""moni.fetch.xwatch_accounts — stable account-timeline discovery (v1.0.12).

Why this exists
---------------
Keyword discovery (DDG Lite) is free but IP-fragile.  Account watching
pulls recent posts for a fixed list of handles via **RSS** when we can
resolve a feed URL — primarily the same ``api.xgo.ing`` feeds already
used by ``bestblog-x`` (read from ``config/bestblogs_x.opml``).

That path does **not** depend on DuckDuckGo, so when DDG is under
anomaly lockdown, xwatch can still produce articles from watched
accounts.

Resolution order per handle
---------------------------
1. ``fetch_config.account_rss[handle]`` — user override
2. Auto-lookup in ``config/bestblogs_x.opml`` (``text`` like ``Name(@handle)``)
3. Fallback: DDG query ``from:handle`` (shares the DDG circuit; optional)

Usage::

    from moni.fetch.xwatch_accounts import fetch_account_tweet_urls
    urls = fetch_account_tweet_urls("op7418", max_results=5)
"""
from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from functools import lru_cache
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20
USER_AGENT = "moni-xwatch/1.0.12 (+https://github.com/mariusiaowego-commits/moni)"

# Project root = moni/fetch/../../
_PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
_DEFAULT_OPML = _PROJECT_ROOT / "config" / "bestblogs_x.opml"

# Match x.com / twitter.com status URLs in RSS bodies.
_STATUS_RE = re.compile(
    r"https?://(?:(?:www|mobile)\.)?(?:x|twitter)\.com/"
    r"(?:#!/)?([A-Za-z0-9_]+)/status(?:es)?/(\d+)",
    re.IGNORECASE,
)
# OPML text like: 歸藏(guizang.ai)(@op7418)
_OPML_HANDLE_RE = re.compile(r"\(@([A-Za-z0-9_]+)\)\s*$")

# Strip leading @ and lowercase for map keys.
_HANDLE_CLEAN_RE = re.compile(r"^@+")


def normalize_handle(handle: str) -> str:
    """Return bare handle without leading @, original case preserved for URLs."""
    if not handle:
        return ""
    return _HANDLE_CLEAN_RE.sub("", handle.strip())


def _handle_key(handle: str) -> str:
    return normalize_handle(handle).lower()


@lru_cache(maxsize=2)
def load_opml_handle_rss(opml_path: str | None = None) -> dict[str, str]:
    """Map lowercase handle → xmlUrl from bestblog-x OPML.

    Cached in-process; pass a different path string to bust for tests.
    """
    path = Path(opml_path) if opml_path else _DEFAULT_OPML
    out: dict[str, str] = {}
    if not path.is_file():
        logger.debug("[xwatch] OPML not found at %s", path)
        return out
    try:
        root = ET.parse(path).getroot()
    except Exception as exc:
        logger.warning("[xwatch] failed to parse OPML %s: %s", path, exc)
        return out

    for outline in root.iter("outline"):
        text = outline.get("text") or outline.get("title") or ""
        xml_url = (outline.get("xmlUrl") or "").strip()
        if not xml_url:
            continue
        m = _OPML_HANDLE_RE.search(text)
        if not m:
            continue
        out[_handle_key(m.group(1))] = xml_url
    logger.debug("[xwatch] OPML handle map size=%d from %s", len(out), path)
    return out


def resolve_account_rss(
    handle: str,
    *,
    account_rss: dict[str, str] | None = None,
    opml_path: str | None = None,
) -> str | None:
    """Resolve an RSS feed URL for ``handle``, or None if unknown."""
    key = _handle_key(handle)
    if not key:
        return None
    if account_rss:
        # Accept both bare and @-prefixed keys in config.
        for k, v in account_rss.items():
            if _handle_key(str(k)) == key and v:
                return str(v).strip()
    opml_map = load_opml_handle_rss(opml_path)
    return opml_map.get(key)


def _canonical_tweet_url(screen_name: str, tweet_id: str) -> str:
    return f"https://x.com/{screen_name}/status/{tweet_id}"


def extract_tweet_urls_from_rss(xml_text: str, *, max_results: int = 10) -> list[str]:
    """Pull canonical tweet URLs out of an RSS/Atom document.

    Scans ``link``, ``guid``, and full text.  Dedupes by tweet id,
    preserves encounter order, caps at ``max_results``.
    """
    if not xml_text:
        return []
    seen_ids: set[str] = set()
    out: list[str] = []

    def _add(screen: str, tid: str) -> bool:
        if tid in seen_ids:
            return False
        seen_ids.add(tid)
        # Prefer the screen name from the URL; fall back to "i".
        name = screen or "i"
        out.append(_canonical_tweet_url(name, tid))
        return len(out) >= max_results

    # Pass 1: structured tags via regex on the whole document (robust to
    # namespaces and broken XML that ElementTree might choke on).
    for m in _STATUS_RE.finditer(xml_text):
        if _add(m.group(1), m.group(2)):
            return out

    # Pass 2: bare /status/<digits> without host (some RSS only put path).
    for m in re.finditer(r"/status(?:es)?/(\d+)", xml_text):
        if _add("i", m.group(1)):
            return out

    return out


def fetch_account_rss_urls(
    handle: str,
    *,
    rss_url: str,
    max_results: int = 10,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[str]:
    """HTTP GET an account RSS feed and extract tweet URLs.  Never raises."""
    try:
        resp = httpx.get(
            rss_url,
            headers={"User-Agent": USER_AGENT, "Accept": "application/rss+xml, application/xml, text/xml, */*"},
            timeout=timeout,
            follow_redirects=True,
        )
        resp.raise_for_status()
    except Exception as exc:
        logger.warning(
            "[xwatch] account RSS failed for @%s (%s): %s",
            normalize_handle(handle), urlparse(rss_url).netloc, exc,
        )
        return []

    urls = extract_tweet_urls_from_rss(resp.text or "", max_results=max_results)
    if not urls:
        logger.info(
            "[xwatch] account RSS @%s returned 0 tweet URLs (len=%d)",
            normalize_handle(handle), len(resp.text or ""),
        )
    return urls


def fetch_account_tweet_urls(
    handle: str,
    *,
    max_results: int = 10,
    timeout: int = DEFAULT_TIMEOUT,
    account_rss: dict[str, str] | None = None,
    opml_path: str | None = None,
    allow_ddg_fallback: bool = True,
    ddg_search: Any | None = None,
) -> tuple[list[str], str]:
    """Discover recent tweet URLs for one account.

    Returns ``(urls, channel)`` where channel is one of:
    ``rss``, ``ddg``, ``none``.

    ``ddg_search`` is injectable (defaults to ``search_tweet_urls``) so
    tests can mock without patching the discovery module path.
    """
    bare = normalize_handle(handle)
    if not bare:
        return [], "none"

    rss = resolve_account_rss(bare, account_rss=account_rss, opml_path=opml_path)
    if rss:
        urls = fetch_account_rss_urls(
            bare, rss_url=rss, max_results=max_results, timeout=timeout,
        )
        if urls:
            return urls, "rss"
        # RSS resolved but empty/fail — still try DDG if allowed.
        logger.info(
            "[xwatch] account @%s RSS empty/fail, fallback=%s",
            bare, "ddg" if allow_ddg_fallback else "none",
        )

    if not allow_ddg_fallback:
        return [], "none"

    if ddg_search is None:
        from .xwatch_discovery import search_tweet_urls as ddg_search  # type: ignore

    try:
        # site:x.com is added inside search_tweet_urls.
        urls = ddg_search(f"from:{bare}", max_results=max_results, timeout=timeout)
    except Exception as exc:
        logger.warning("[xwatch] account DDG fallback @%s failed: %s", bare, exc)
        return [], "none"
    return (urls or []), ("ddg" if urls else "none")
