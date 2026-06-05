"""Fetcher36krNewsflash — 36kr newsflash HTML page (https://36kr.com/newsflashes)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

import httpx
from bs4 import BeautifulSoup

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

FEED_URL = "https://36kr.com/newsflashes"


@register_fetcher
class Fetcher36krNewsflash(BaseFetcher):
    source_name: str = "36kr_newsflash"

    def fetch(self) -> list[dict[str, Any]]:
        try:
            resp = httpx.get(FEED_URL, timeout=15.0)
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error("[36kr_newsflash] HTTP %s fetching %s", exc.response.status_code, exc.request.url)
            return []
        except httpx.TimeoutException:
            logger.error("[36kr_newsflash] Timeout fetching %s", FEED_URL)
            return []
        except Exception as exc:
            logger.error("[36kr_news_newsflash] Unexpected error fetching %s: %s", FEED_URL, exc)
            return []

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            logger.error("[36kr_newsflash] Failed to parse HTML: %s", exc)
            return []

        articles: list[dict[str, Any]] = []

        # Newsflash items live in <a class="newsflash-item"> or similar.
        # The 36kr newsflash page uses a list of items with class "newsflash-item".
        # Each item has: title in <span class="newsflash-title">,
        #   link in href, and time in <span class="newsflash-time">.
        items = soup.find_all("a", class_=re.compile(r"newsflash"))
        if not items:
            # Fallback: any <a> with a time sibling that looks like a newsflash
            items = soup.find_all("a", href=re.compile(r"/newsflash/"))

        for item in items:
            try:
                href = str(item.get("href") or "")
                if not href:
                    continue

                # Ensure absolute URL
                if href.startswith("/"):
                    href = "https://36kr.com" + href
                elif not href.startswith("http"):
                    continue

                title_el = item.find(class_=re.compile(r"title"))
                title = title_el.get_text(strip=True) if title_el else item.get_text(strip=True)

                time_el = item.find(class_=re.compile(r"time"))
                published_at: str | None = None
                if time_el:
                    time_text = time_el.get_text(strip=True)
                    published_at = self._parse_time(time_text)

                articles.append({
                    "title": title,
                    "url": href,
                    "source": self.source_name,
                    "published_at": published_at,
                    "content": "",
                })
            except Exception as exc:
                logger.warning("[36kr_newsflash] Error parsing newsflash item: %s — skipping", exc)

        logger.info("[36kr_newsflash] Found %d newsflash items", len(articles))
        return articles

    def _parse_time(self, time_text: str) -> str | None:
        """Parse relative or absolute time strings from 36kr."""
        now = datetime.now(timezone.utc)
        # "2小时前", "3分钟前", "昨天 10:30"
        m = re.search(r"(\d+)\s*小时前", time_text)
        if m:
            hours = int(m.group(1))
            dt = datetime.fromtimestamp(now.timestamp() - hours * 3600, tz=timezone.utc)
            return dt.isoformat()
        m = re.search(r"(\d+)\s*分钟前", time_text)
        if m:
            minutes = int(m.group(1))
            dt = datetime.fromtimestamp(now.timestamp() - minutes * 60, tz=timezone.utc)
            return dt.isoformat()
        # "昨天 10:30"
        m = re.search(r"昨天\s*(\d+):(\d+)", time_text)
        if m:
            yesterday = now.replace(day=now.day - 1) if now.day > 1 else now
            dt = yesterday.replace(hour=int(m.group(1)), minute=int(m.group(2)), second=0, microsecond=0)
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
