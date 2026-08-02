"""moni.fetch.xwatch_discovery — DuckDuckGo Lite scraper for finding X tweet URLs.

Background
----------
v1.0.11 originally called ``html.duckduckgo.com/html/`` (no API key, no
login), but from this project's IP range that endpoint started returning
"Anomaly" challenge pages for nearly every query — not usable in
practice.  v1.0.11.A switches to ``lite.duckduckgo.com/lite/``, the
stripped-down sibling endpoint.  Lite uses a different HTML shape:

    <a class="result-link" rel="nofollow"
       href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fx.com%2Fwxapp%2Fstatus%2F111...">
       Title text
    </a>

We pull the ``uddg=`` query parameter, URL-decode it, and run the same
tweet-URL regex on the decoded target.  If lite also gets blocked we
degrade to ``[]`` (logged), never raise, never switch providers.

Why no other surface
--------------------
x.com / twitter.com both require auth for any embedded tweet search;
syndication's tweet-result endpoint only returns a single tweet at a
time, no discovery.  Bing and Brave search also surface tweets but cost
real money in our volume range.  DDG is the only free surface; if it
dies on this IP, xwatch gracefully degrades (returns nothing) and the
manual ``moni xwatch ingest <url>`` path remains available.

Usage::

    from moni.fetch.xwatch_discovery import search_tweet_urls
    urls = search_tweet_urls("微信小程序 触达", max_results=20)
"""
from __future__ import annotations

import logging
import re
import time
from html import unescape
from urllib.parse import parse_qs, urlparse

import httpx

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 20
# v1.0.11.A: switched from html.duckduckgo.com/html/ to lite.duckduckgo.com/lite/.
# If/when lite also gets blocked we degrade to [] + log; never escalate to
# a paid provider, never pause the feature.
DDG_URL = "https://lite.duckduckgo.com/lite/"
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15"
)

# Match `x.com/<user>/status/<id>` — the same regex is used both as the
# canonical extractor and as the fallback sweep over raw HTML.  Stays
# identical to the v1.0.11 pre-A behaviour so tests don't break.
TWEET_URL_RE = re.compile(r"x\.com/([A-Za-z0-9_]+)/status/(\d+)")

# DDG wraps outbound URLs through //duckduckgo.com/l/?uddg=<encoded>.
# Recognise that wrapper so we can peel back to the actual target.
DDG_REDIRECT_PREFIX_RE = re.compile(r"//duckduckgo\.com/l/\?uddg=")
DDG_HREF_RE = re.compile(r'href="([^"]+)"', re.IGNORECASE)


def _is_anomaly_page(html: str) -> bool:
    """Cheap heuristic to recognise DDG's anti-bot challenge page.

    Lite's challenge page sets distinctive class names; looking at the
    full HTML is more reliable than title-sniffing.
    """
    if not html:
        return False
    h = html.lower()
    if "anomaly-modal" in h:
        return True
    if "unfortunately, bots use duckduckgo too" in h:
        return True
    return False


def _extract_tweet_urls_from_lite_html(html: str, max_results: int = 20) -> list[str]:
    """Pull tweet URLs out of a DuckDuckGo Lite result page.

    Two passes, both intentionally loose so DDG link-shape changes
    don't immediately break us:

    1. Every ``<a href="...">`` attribute on the page.  When the href
       points at DDG's redirect wrapper (``//duckduckgo.com/l/?uddg=...``)
       we URL-decode the ``uddg`` value and run the tweet regex on that.
    2. Fallback: a single regex sweep over the whole HTML for any
       literal ``x.com/.../status/<digits>`` substring — covers the
       case where DDG switched link shape again but the canonical URL
       survives somewhere in a snippet or text attribute.

    Output is deduped in encounter order, capped at ``max_results``.
    """
    seen: set[str] = set()
    out: list[str] = []
    if not html:
        return out

    # Pass 1: walk every href attribute.
    for attr_match in DDG_HREF_RE.finditer(html):
        raw_href = attr_match.group(1)
        # Unescape minimal HTML entities DDG might emit ("&amp;" etc.).
        unescaped = (
            raw_href
            .replace("&amp;", "&")
            .replace("&lt;", "<")
            .replace("&gt;", ">")
            .replace("&quot;", '"')
        )
        target = unescape(unescaped)

        # If this href is a DDG redirect wrapper, peel it open.
        if DDG_REDIRECT_PREFIX_RE.search(target):
            try:
                full_url = target if target.startswith("http") else "https:" + target
                parsed = urlparse(full_url)
                q = parse_qs(parsed.query)
                uddg = q.get("uddg", [""])
                if uddg and uddg[0]:
                    target = unescape(uddg[0])
            except Exception:
                # Malformed uddg — fall through to regex pass.
                continue

        for url_match in TWEET_URL_RE.finditer(target):
            screen_name, tweet_id = url_match.group(1), url_match.group(2)
            url = f"https://x.com/{screen_name}/status/{tweet_id}"
            if url in seen:
                continue
            seen.add(url)
            out.append(url)
            if len(out) >= max_results:
                return out

    # Pass 2 (fallback): sweep raw HTML for any surviving x.com URL.
    for match in TWEET_URL_RE.finditer(html):
        screen_name, tweet_id = match.group(1), match.group(2)
        url = f"https://x.com/{screen_name}/status/{tweet_id}"
        if url in seen:
            continue
        seen.add(url)
        out.append(url)
        if len(out) >= max_results:
            break

    return out


def search_tweet_urls(
    query: str,
    *,
    max_results: int = 20,
    timeout: int = DEFAULT_TIMEOUT,
) -> list[str]:
    """Return up to ``max_results`` tweet URLs from a single DDG query.

    Behaviour notes
    ---------------
    * The user query is rewritten to ``site:x.com <query>`` so DDG
      filters to x.com results — cheapest way to avoid polluting the
      result with mirror sites / aggregators.
    * On anomaly / 风控 response we sleep 5 seconds and retry ONCE.
      Still failing yields ``[]`` and a warning log.  We do not switch
      providers, do not pause the feature.
    * Any other network / parse error also yields ``[]`` (never raises).
    """
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    full_query = f"site:x.com {query}"
    params = {"q": full_query}

    for attempt in (1, 2):
        try:
            resp = httpx.get(
                DDG_URL,
                params=params,
                headers=headers,
                timeout=timeout,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except Exception as exc:
            logger.warning(
                "[xwatch] ddg request failed (attempt %d, query=%r): %s",
                attempt, query, exc,
            )
            # Don't retry on plain network errors — they usually won't
            # resolve with a 5-second sleep.  Fall through to [].
            return []

        html = resp.text or ""
        if attempt == 1 and _is_anomaly_page(html):
            logger.warning(
                "[xwatch] ddg anomaly for %r, sleeping 5s and retrying once",
                query,
            )
            time.sleep(5)
            continue  # retry once

        urls = _extract_tweet_urls_from_lite_html(html, max_results)
        if not urls and _is_anomaly_page(html):
            # 2nd request, still anomaly — counts as anomaly persist.
            logger.warning(
                "[xwatch] ddg anomaly persists for %r, returning []", query,
            )
            return []
        return urls

    # Defensive: shouldn't reach here, but if the loop exits without a
    # return it means we retried twice (anomaly, anomaly) — degrade.
    logger.warning("[xwatch] ddg anomaly persists for %r, returning []", query)
    return []


# Back-compat alias — the existing test suite (tests/test_xwatch_discovery.py)
# imports ``_extract_tweet_urls`` directly.  Same input/output contract:
# HTML in, deduped list of canonical x.com status URLs out.
_extract_tweet_urls = _extract_tweet_urls_from_lite_html
