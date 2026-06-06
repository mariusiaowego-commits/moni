"""Fetcher36krNewsflash — 36kr newsflash HTML page (https://36kr.com/newsflashes).

v1.0.3: 36kr's newsflash page is server-rendered with React but the static HTML
contains a list of items inside ``<div class="newsflash-item">`` containers.
Each container has:
  - ``<a class="item-title" href="/newsflashes/<id>">TITLE</a>``
  - ``<span class="time">X分钟前 / X小时前 / 昨天 HH:MM</span>``
The earlier version of this fetcher looked for ``<a class="newsflash-...">``,
which does not exist in the static HTML, so it always returned 0 items.

Also adds a User-Agent header — 36kr's nginx does not require it, but having a
realistic UA matches the other fetchers and reduces the chance of a future
CDN returning 403.
"""

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

# Shared headers — used by all v1.0.3 fetchers. Kept module-level so subclasses
# (or future tests) can reuse.
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
class Fetcher36krNewsflash(BaseFetcher):
    source_name: str = "36kr_newsflash"

    def fetch(self) -> list[dict[str, Any]]:
        # v1.0.5.5: same cookie bypass as Fetcher36krRSS — see the
        # note in that module.  load_cookies_for is a no-op if the
        # cookie file is missing.
        from ..cookie_loader import load_cookies_for

        cookies = load_cookies_for("36kr")
        try:
            resp = httpx.get(
                FEED_URL,
                headers=DEFAULT_HEADERS,
                cookies=cookies or None,
                timeout=15.0,
                follow_redirects=True,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "[36kr_newsflash] HTTP %s fetching %s",
                exc.response.status_code,
                exc.request.url,
            )
            return []
        except httpx.TimeoutException:
            logger.error("[36kr_newsflash] Timeout fetching %s", FEED_URL)
            return []
        except Exception as exc:
            logger.error(
                "[36kr_newsflash] Unexpected error fetching %s: %s", FEED_URL, exc
            )
            return []

        try:
            soup = BeautifulSoup(resp.text, "html.parser")
        except Exception as exc:
            logger.error("[36kr_newsflash] Failed to parse HTML: %s", exc)
            return []

        # v1.0.3: detect captcha / bot-challenge page and warn clearly.
        # 36kr's edge returns a small HTML page with TTGCaptcha when the
        # request is fingerprint-flagged. We do not crash — the captcha
        # is a transient infra issue, not a fetcher bug.
        if "TTGCaptcha" in resp.text or "captcha" in resp.text.lower() and len(resp.text) < 5000:
            logger.warning(
                "[36kr_newsflash] 36kr returned a captcha challenge page "
                "(size=%d). Fetching will be skipped until the bot-detection "
                "cools down. This is not a fetcher bug.",
                len(resp.text),
            )
            return []

        articles: list[dict[str, Any]] = []

        # v1.0.3: 36kr wraps each newsflash item in <div class="newsflash-item">.
        # The <a class="item-title"> child holds the title + href.
        items = soup.find_all("div", class_="newsflash-item")

        for item in items:
            try:
                a = item.find("a", class_="item-title")
                if not a:
                    continue
                href = str(a.get("href") or "")
                if not href:
                    continue

                if href.startswith("/"):
                    href = "https://36kr.com" + href
                elif not href.startswith("http"):
                    continue

                title = a.get_text(strip=True)
                if not title:
                    continue

                # Time: <span class="time">X分钟前 / X小时前 / 昨天 HH:MM</span>
                published_at: str | None = None
                time_el = item.find("span", class_="time")
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
                logger.warning(
                    "[36kr_newsflash] Error parsing newsflash item: %s — skipping",
                    exc,
                )

        logger.info("[36kr_newsflash] Found %d newsflash items", len(articles))
        return articles

    def _parse_time(self, time_text: str) -> str | None:
        """Parse relative or absolute time strings from 36kr."""
        now = datetime.now(timezone.utc)
        m = re.search(r"(\d+)\s*小时前", time_text)
        if m:
            hours = int(m.group(1))
            dt = datetime.fromtimestamp(
                now.timestamp() - hours * 3600, tz=timezone.utc
            )
            return dt.isoformat()
        m = re.search(r"(\d+)\s*分钟前", time_text)
        if m:
            minutes = int(m.group(1))
            dt = datetime.fromtimestamp(
                now.timestamp() - minutes * 60, tz=timezone.utc
            )
            return dt.isoformat()
        m = re.search(r"昨天\s*(\d+):(\d+)", time_text)
        if m:
            # "昨天 HH:MM" — use yesterday at HH:MM (local-naive; UTC-offset varies
            # by server TZ, but the result is just a relative anchor).
            try:
                yesterday = now.replace(day=now.day - 1)
            except ValueError:
                # day=0 would happen on day 1 of month → fall back to subtracting
                # 24h
                from datetime import timedelta
                yesterday = now - timedelta(days=1)
            dt = yesterday.replace(
                hour=int(m.group(1)),
                minute=int(m.group(2)),
                second=0,
                microsecond=0,
            )
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
