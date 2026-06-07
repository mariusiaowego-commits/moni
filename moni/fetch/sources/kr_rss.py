"""Fetcher36krRSS — 36kr RSS feed (https://36kr.com/feed).

v1.0.5.5: switched primary fetch path to a real Chrome instance (via
``moni.fetch.browser_harness``).  Plain ``httpx`` against 36kr.com
always returns the TTGCaptcha challenge page (1460 bytes) because the
edge server fingerprints the client — Go's TLS fingerprint, lack of
JS execution, missing cookies.  A real Chrome with the user's login
cookies (stored in Profile 7, populated by the user once via a headed
session) bypasses the gate and the page content is the real RSS XML.

We try the browser path first.  If Chrome isn't available (e.g. daemon
not started, headless blocked), we fall back to the old ``httpx`` +
``load_cookies_for`` path.  Both paths converge into the same RSS XML
parser below — only the source of the bytes differs.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
import re
from xml.etree import ElementTree as ET

import httpx

from ..base import BaseFetcher, register_fetcher
from ..browser_harness import fetch_body_innertext
from ..cookie_loader import load_cookies_for

logger = logging.getLogger(__name__)

FEED_URL = "https://36kr.com/feed"


def _fetch_via_browser() -> str | None:
    """Primary path: real Chrome + body.innerText.

    Returns the page body as text (which for /feed IS the RSS XML), or
    None if the browser path failed for any reason.
    """
    return fetch_body_innertext(FEED_URL, settle_s=4.0)


def _fetch_via_httpx() -> str | None:
    """Fallback path: httpx + persisted cookies.

    Kept for environments where running a Chrome daemon is not
    practical (CI, sandboxed containers, headless servers).  Almost
    always returns the TTGCaptcha page in production — the captcha
    detection in ``fetch()`` handles that gracefully.
    """
    cookies = load_cookies_for("36kr")
    try:
        resp = httpx.get(
            FEED_URL,
            timeout=15.0,
            cookies=cookies or None,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/148.0.0.0 Safari/537.36"
                ),
            },
        )
        resp.raise_for_status()
        return resp.text
    except httpx.HTTPStatusError as exc:
        logger.error(
            "[36kr_rss] HTTP %s fetching %s",
            exc.response.status_code, exc.request.url,
        )
        return None
    except httpx.TimeoutException:
        logger.error("[36kr_rss] Timeout fetching %s", FEED_URL)
        return None
    except Exception as exc:
        logger.error("[36kr_rss] Unexpected error fetching %s: %s", FEED_URL, exc)
        return None


@register_fetcher
class Fetcher36krRSS(BaseFetcher):
    source_name: str = "36kr_rss"

    def fetch(self) -> list[dict[str, Any]]:
        # Try the browser path first — only way to bypass TTGCaptcha.
        # Fall back to httpx for environments without Chrome.
        body = _fetch_via_browser()
        if body is None:
            logger.info("[36kr_rss] browser path failed — falling back to httpx")
            body = _fetch_via_httpx()
        if not body:
            return []

        # Captcha detection: the challenge page is small and contains
        # either the literal "TTGCaptcha" string or a captcha SDK url.
        # RSS body is typically 30KB+ — anything under 5KB is suspect.
        if ("TTGCaptcha" in body or "captcha" in body.lower()) and len(body) < 5000:
            logger.warning(
                "[36kr_rss] 36kr returned a captcha challenge page "
                "(size=%d). Skipping — re-run with MONI_36KR_HEADED=1 "
                "and complete the captcha in the headed browser.",
                len(body),
            )
            return []

        try:
            root = ET.fromstring(body.encode("utf-8"))
        except Exception as exc:
            logger.error("[36kr_rss] Failed to parse RSS XML: %s", exc)
            return []

        # RSS 2.0: <channel> → <item>
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else []

        articles: list[dict[str, Any]] = []
        for item in items:
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                description = (item.findtext("description") or "").strip()
                pub_text = item.findtext("pubDate")

                published_at: str | None = None
                if pub_text:
                    normalized = re.sub(r"\s{2,}", " ", pub_text.strip())
                    try:
                        # RFC 822 with day name: "Thu, 05 Jun 2026 10:00:00 +0000"
                        dt = datetime.strptime(normalized, "%a, %d %b %Y %H:%M:%S %z")
                        published_at = dt.isoformat()
                    except ValueError:
                        try:
                            # 36kr custom format: "2026-06-05 14:52:26  +0800" (double-space before tz)
                            dt = datetime.strptime(normalized, "%Y-%m-%d %H:%M:%S %z")
                            published_at = dt.isoformat()
                        except ValueError:
                            try:
                                dt = datetime.fromisoformat(pub_text.replace("Z", "+00:00"))
                                published_at = dt.isoformat()
                            except ValueError:
                                logger.warning("[36kr_rss] Unparseable pubDate %r — skipping", pub_text)

                articles.append({
                    "title": title,
                    "url": link,
                    "source": self.source_name,
                    "published_at": published_at,
                    "content": description,
                })
            except Exception as exc:
                logger.warning("[36kr_rss] Error parsing RSS item: %s — skipping", exc)

        return articles

    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        if not raw.get("url"):
            return None
        return {
            "title": raw.get("title") or "",
            "url": raw.get("url"),
            "source": self.source_name,
            "published_at": raw.get("published_at"),
            "content": raw.get("content") or "",
        }
