"""FetcherMpaypass — mpaypass.com.cn HTML feed (https://mpaypass.com.cn/)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

FEED_URL = "https://mpaypass.com.cn/"


@register_fetcher
class FetcherMpaypass(BaseFetcher):
    source_name: str = "mpaypass"

    def fetch(self) -> list[dict[str, Any]]:
        try:
            resp = httpx.get(FEED_URL, timeout=15.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("[mpaypass] HTTP %s fetching %s", exc.response.status_code, exc.request.url)
            return []
        except httpx.TimeoutException:
            logger.error("[mpaypass] Timeout fetching %s", FEED_URL)
            return []
        except Exception as exc:
            logger.error("[mpaypass] Unexpected error fetching %s: %s", FEED_URL, exc)
            return []

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            logger.error("[mpaypass] Failed to parse HTML: %s", exc)
            return []

        articles: list[dict[str, Any]] = []

        # mpaypass uses a standard news list; look for article links
        # Common patterns: <a href="/article/...">, or list items with .article-title
        for a in soup.find_all("a", href=re.compile(r"/\d+\.html")):
            try:
                href = str(a.get("href") or "")
                if not href:
                    continue

                if href.startswith("/"):
                    href = "https://mpaypass.com.cn" + href
                elif not href.startswith("http"):
                    continue

                # Title: from the <a> text or nested .title/.article-title span
                title_el = a.find(class_=re.compile(r"title"))
                title = title_el.get_text(strip=True) if title_el else a.get_text(strip=True)
                if not title:
                    continue

                # Try to find a nearby time element
                published_at: str | None = None
                parent = a.find_parent(["li", "div"])
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
                logger.warning("[mpaypass] Error parsing item: %s — skipping", exc)

        # Dedupe by URL
        seen: set[str] = set()
        unique: list[dict[str, Any]] = []
        for art in articles:
            if art["url"] not in seen:
                seen.add(art["url"])
                unique.append(art)
        articles = unique

        logger.info("[mpaypass] Found %d articles", len(articles))
        return articles

    def _parse_time(self, time_text: str) -> str | None:
        """Parse common Chinese date formats."""
        # "2026-06-05", "2026年06月05日", "06-05", "今天 10:30"
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", time_text)
        if m:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            return dt.isoformat()
        m = re.search(r"(\d{4})年(\d{2})月(\d{2})日", time_text)
        if m:
            dt = datetime(int(m.group(1)), int(m.group(2)), int(m.group(3)), tzinfo=timezone.utc)
            return dt.isoformat()
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
