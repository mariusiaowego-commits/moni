"""FetcherQbitai — qbitai.com HTML feed (https://www.qbitai.com/)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

FEED_URL = "https://www.qbitai.com/"


@register_fetcher
class FetcherQbitai(BaseFetcher):
    source_name: str = "qbitai"

    def fetch(self) -> list[dict[str, Any]]:
        try:
            resp = httpx.get(FEED_URL, timeout=15.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("[qbitai] HTTP %s fetching %s", exc.response.status_code, exc.request.url)
            return []
        except httpx.TimeoutException:
            logger.error("[qbitai] Timeout fetching %s", FEED_URL)
            return []
        except Exception as exc:
            logger.error("[qbitai] Unexpected error fetching %s: %s", FEED_URL, exc)
            return []

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            logger.error("[qbitai] Failed to parse HTML: %s", exc)
            return []

        articles: list[dict[str, Any]] = []

        # qbitai uses article links with numeric paths; find all article <a> tags
        for a in soup.find_all("a", href=re.compile(r"/\d{4,}/")):
            try:
                href = str(a.get("href") or "")
                if not href:
                    continue

                if href.startswith("/"):
                    href = "https://www.qbitai.com" + href
                elif not href.startswith("http"):
                    continue

                # Title from inner text or nested .title element
                title_el = a.find(class_=re.compile(r"title|headline"))
                title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
                if not title:
                    continue

                # Filter out nav/menu links
                if any(skip in href for skip in ["/category/", "/tag/", "/author/"]):
                    continue

                # Find time if present
                published_at: str | None = None
                parent = a.find_parent(["li", "div", "article"])
                if parent:
                    time_el = parent.find(class_=re.compile(r"time|date|created"))
                    if time_el:
                        published_at = self._parse_time(time_el.get_text(strip=True))

                articles.append({
                    "title": title,
                    "url": href,
                    "source": self.source_name,
                    "published_at": published_at,
                    "content": "",
                })
            except Exception as exc:
                logger.warning("[qbitai] Error parsing item: %s — skipping", exc)

        # Dedupe by URL
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for art in articles:
            if art["url"] not in seen:
                seen.add(art["url"])
                unique.append(art)
        articles = unique

        logger.info("[qbitai] Found %d articles", len(articles))
        return articles

    def _parse_time(self, time_text: str) -> str | None:
        """Parse common date formats."""
        # "2026-06-05", "2026年06月05日", "Jun 5, 2026"
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", time_text)
        if m:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc).isoformat()
        m = re.search(r"(\d{4})年(\d{2})月(\d{2})日", time_text)
        if m:
            return datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc).isoformat()
        m = re.search(r"([A-Z][a-z]{2})\s+(\d{1,2}),\s+(\d{4})", time_text)
        if m:
            months = {"Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
                      "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12}
            month = months.get(m.group(1))
            if month:
                return datetime(int(m.group(3)), month, int(m.group(2)), tzinfo=timezone.utc).isoformat()
        return None

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
