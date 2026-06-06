"""FetcherQbitai — qbitai.com RSS feed (https://www.qbitai.com/feed).

v1.0.3: the homepage (https://www.qbitai.com/) is a Cloudflare-protected WordPress
page that returns HTTP 403 to ``httpx`` without a realistic User-Agent. The RSS
feed (https://www.qbitai.com/feed) is publicly served by WordPress and returns
clean RSS 2.0 XML with 10 items per fetch. Switched from HTML scraping to RSS
parsing (same pattern as ``kr_rss.py``).
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

FEED_URL = "https://www.qbitai.com/feed"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}


@register_fetcher
class FetcherQbitai(BaseFetcher):
    source_name: str = "qbitai"

    def fetch(self) -> list[dict[str, Any]]:
        try:
            resp = httpx.get(
                FEED_URL,
                headers=DEFAULT_HEADERS,
                timeout=15.0,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "[qbitai] HTTP %s fetching %s",
                exc.response.status_code,
                exc.request.url,
            )
            return []
        except httpx.TimeoutException:
            logger.error("[qbitai] Timeout fetching %s", FEED_URL)
            return []
        except Exception as exc:
            logger.error("[qbitai] Unexpected error fetching %s: %s", FEED_URL, exc)
            return []

        try:
            root = ET.fromstring(resp.text.encode("utf-8"))
        except Exception as exc:
            logger.error("[qbitai] Failed to parse RSS XML: %s", exc)
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
                        # RFC 822: "Fri, 05 Jun 2026 13:16:53 +0000"
                        dt = datetime.strptime(
                            normalized, "%a, %d %b %Y %H:%M:%S %z"
                        )
                        published_at = dt.isoformat()
                    except ValueError:
                        logger.debug(
                            "[qbitai] Unparseable pubDate %r", pub_text
                        )

                if not title or not link:
                    continue

                articles.append({
                    "title": title,
                    "url": link,
                    "source": self.source_name,
                    "published_at": published_at,
                    "content": description,
                })
            except Exception as exc:
                logger.warning("[qbitai] Error parsing RSS item: %s — skipping", exc)

        logger.info("[qbitai] Found %d RSS items", len(articles))
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
