"""FetcherHuxiu — huxiu.com RSS feed (https://rss.huxiu.com/).

虎嗅 RSS 返回标准 RSS 2.0 XML，description 包含全文 HTML。
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

FEED_URL = "https://rss.huxiu.com/"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


@register_fetcher
class FetcherHuxiu(BaseFetcher):
    source_name: str = "huxiu"

    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Pass-through — fetch() already returns canonical schema."""
        return raw

    def fetch(self) -> list[dict[str, Any]]:
        """Fetch articles from huxiu RSS feed."""
        resp = httpx.get(FEED_URL, headers=DEFAULT_HEADERS, timeout=15, follow_redirects=True)
        resp.raise_for_status()
        root = ET.fromstring(resp.text)

        articles = []
        for item in root.iter("item"):
            title = (item.findtext("title") or "").strip()
            link = (item.findtext("link") or "").strip()
            desc = (item.findtext("description") or "").strip()
            author = (item.findtext("author") or "").strip()
            pub_date = (item.findtext("pubDate") or "").strip()

            # Parse pubDate (RFC 2822 format: "Tue, 16 Jun 2026 12:47:00 +0800")
            published_at = None
            if pub_date:
                try:
                    from email.utils import parsedate_to_datetime
                    published_at = parsedate_to_datetime(pub_date).isoformat()
                except Exception:
                    pass

            # Clean HTML from description to get plain text
            content = self._clean_html(desc) if desc else ""

            articles.append({
                "title": title,
                "url": link,
                "content": content,
                "author": author,
                "published_at": published_at,
            })

        return articles

    def _clean_html(self, html: str) -> str:
        """Strip HTML tags and boilerplate, keep text content."""
        from ..content import normalize

        return normalize(html).text
