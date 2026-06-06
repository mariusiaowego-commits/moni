"""Fetcher36krRSS — 36kr RSS feed (https://36kr.com/feed)."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any
import re
from xml.etree import ElementTree as ET

import httpx

from ..base import BaseFetcher, register_fetcher
from ..cookie_loader import load_cookies_for

logger = logging.getLogger(__name__)

FEED_URL = "https://36kr.com/feed"


@register_fetcher
class Fetcher36krRSS(BaseFetcher):
    source_name: str = "36kr_rss"

    def fetch(self) -> list[dict[str, Any]]:
        # v1.0.5.5: load persisted cookies from
        # ``config/36kr_cookies.json`` (dumped by the user from a real
        # Chrome session).  36kr.com returns the TTGCaptcha challenge
        # page to vanilla httpx; a known krtoken/userId cookie set
        # bypasses the challenge gate.  Cookie set is optional — if
        # missing, the request still goes out and the parse path
        # below handles the captcha page gracefully (return []).
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
        except httpx.HTTPStatusError as exc:
            logger.error("[36kr_rss] HTTP %s fetching %s", exc.response.status_code, exc.request.url)
            return []
        except httpx.TimeoutException:
            logger.error("[36kr_rss] Timeout fetching %s", FEED_URL)
            return []
        except Exception as exc:
            logger.error("[36kr_rss] Unexpected error fetching %s: %s", FEED_URL, exc)
            return []

        try:
            root = ET.fromstring(resp.text.encode("utf-8"))
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
