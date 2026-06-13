"""FetcherBestBlogs — aggregated RSS from BestBlogs OPML.

Fetches an OPML file listing N wechat2rss feeds, then pulls each
feed with configurable limits (max_feeds / max_articles_per_feed /
max_articles_total).  Reuses the same RSS 2.0 parsing logic as
FetcherWechatRSS.

Config is stored in ``sources.fetch_config`` (JSON)::

    {
        "max_feeds": 20,            // how many OPML entries to process
        "max_articles_per_feed": 5, // latest N items per feed
        "max_articles_total": 50,   // hard cap per fetch run
        "request_delay": 0.5        // seconds between HTTP requests
    }
"""
from __future__ import annotations

import json
import logging
import time
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

import re

import httpx

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

# WeChat RSS boilerplate patterns — strip from description before storing.
# These appear in <description> but are NOT article content.
_BOILERPLATE_RE = re.compile(
    r"(?:"
    r"点击关注.*$"
    r"|长按识别.*$"
    r"|扫描二维码.*$"
    r"|分享到朋友圈.*$"
    r"|点击「在看」.*$"
    r"|喜欢这篇文章.*$"
    r"|点击阅读原文.*$"
    r"|阅读原文.*$"
    r"|了解更多.*$"
    r"|原创.*转载请.*$"
    r"|本文来源.*$"
    r"|免责声明.*$"
    r"|商务合作.*$"
    r"|投稿邮箱.*$"
    r"|点击上方.*关注.*$"
    r"|戳「.*」.*$"
    r")",
    re.MULTILINE,
)


def _clean_description(text: str) -> str:
    """Strip WeChat boilerplate from RSS description."""
    cleaned = _BOILERPLATE_RE.sub("", text)
    # Collapse multiple newlines
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()

# Defaults when fetch_config is missing or incomplete
_DEFAULTS = {
    "max_feeds": 375,
    "max_articles_per_feed": 5,
    "max_articles_total": 500,
    "request_delay": 0.5,
}


@register_fetcher
class FetcherBestBlogs(BaseFetcher):
    source_name: str = "bestblogs"

    def fetch(self) -> list[dict[str, Any]]:
        config = self._load_config()
        opml_url = self._resolve_url()
        if not opml_url:
            logger.error("[bestblogs] no OPML URL in sources.url")
            return []

        # 1. Fetch OPML → list of (title, feed_url)
        feeds = self._fetch_opml(opml_url)
        if not feeds:
            return []

        max_feeds = config["max_feeds"]
        if len(feeds) > max_feeds:
            logger.info(
                "[bestblogs] OPML has %d feeds, limiting to %d",
                len(feeds), max_feeds,
            )
        feeds = feeds[:max_feeds]

        # 2. Fetch each feed, collect articles
        all_articles: list[dict[str, Any]] = []
        delay = config["request_delay"]
        max_per_feed = config["max_articles_per_feed"]
        max_total = config["max_articles_total"]

        for i, (feed_title, feed_url) in enumerate(feeds):
            if len(all_articles) >= max_total:
                logger.info(
                    "[bestblogs] reached total cap %d, stopping at feed %d/%d",
                    max_total, i, len(feeds),
                )
                break

            if i > 0 and delay > 0:
                time.sleep(delay)

            items = self._fetch_feed(feed_url, feed_title)
            # Take latest N items per feed
            items = items[:max_per_feed]
            all_articles.extend(items)

            if (i + 1) % 10 == 0:
                logger.info(
                    "[bestblogs] fetched %d/%d feeds, %d articles so far",
                    i + 1, len(feeds), len(all_articles),
                )

        # 3. Hard cap
        if len(all_articles) > max_total:
            all_articles = all_articles[:max_total]

        logger.info(
            "[bestblogs] done — %d feeds, %d articles",
            len(feeds), len(all_articles),
        )
        return all_articles

    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        if not raw.get("url"):
            return None
        return {
            "title": raw.get("title") or "",
            "url": raw["url"],
            "source": self.source_name,
            "published_at": raw.get("published_at"),
            "content": raw.get("content") or "",
            "publisher": raw.get("publisher"),
        }

    # ── internals ──────────────────────────────────────────────────

    def _load_config(self) -> dict:
        """Read fetch_config JSON from sources table, fill defaults."""
        cfg = dict(_DEFAULTS)
        try:
            from ...db.connection import get_db
            conn = get_db()
            row = conn.execute(
                "SELECT fetch_config FROM sources WHERE name = ?",
                (self.source_name,),
            ).fetchone()
            if row and row["fetch_config"]:
                user = json.loads(row["fetch_config"])
                cfg.update({k: v for k, v in user.items() if k in _DEFAULTS})
        except Exception as exc:
            logger.debug("[bestblogs] failed to read fetch_config: %s", exc)
        return cfg

    def _resolve_url(self) -> str:
        """OPML URL from sources.url."""
        try:
            from ...db.connection import get_db
            conn = get_db()
            row = conn.execute(
                "SELECT url FROM sources WHERE name = ?",
                (self.source_name,),
            ).fetchone()
            if row and row["url"]:
                return row["url"]
        except Exception as exc:
            logger.debug("[bestblogs] DB lookup failed: %s", exc)
        return ""

    def _fetch_opml(self, opml_url: str) -> list[tuple[str, str]]:
        """Fetch OPML, return [(title, xmlUrl), ...]."""
        try:
            resp = httpx.get(opml_url, timeout=30.0)
            resp.raise_for_status()
        except Exception as exc:
            logger.error("[bestblogs] failed to fetch OPML: %s", exc)
            return []

        try:
            root = ET.fromstring(resp.text.encode("utf-8"))
        except Exception as exc:
            logger.error("[bestblogs] failed to parse OPML XML: %s", exc)
            return []

        feeds: list[tuple[str, str]] = []
        for outline in root.iter("outline"):
            xml_url = outline.get("xmlUrl", "").strip()
            title = (outline.get("title") or outline.get("text") or "").strip()
            if xml_url:
                feeds.append((title, xml_url))

        logger.info("[bestblogs] OPML parsed: %d feeds", len(feeds))
        return feeds

    def _fetch_feed(
        self, feed_url: str, feed_title: str
    ) -> list[dict[str, Any]]:
        """Fetch one RSS feed, return article dicts."""
        try:
            resp = httpx.get(feed_url, timeout=15.0)
            if resp.status_code == 404:
                logger.debug("[bestblogs] 404: %s", feed_title)
                return []
            resp.raise_for_status()
        except httpx.TimeoutException:
            logger.warning("[bestblogs] timeout: %s", feed_title)
            return []
        except Exception as exc:
            logger.warning("[bestblogs] error fetching %s: %s", feed_title, exc)
            return []

        try:
            root = ET.fromstring(resp.text.encode("utf-8"))
        except Exception:
            return []

        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else []

        articles: list[dict[str, Any]] = []
        for item in items:
            try:
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                description = _clean_description(
                    (item.findtext("description") or "").strip()
                )
                pub_text = item.findtext("pubDate")

                author = (
                    item.findtext("dc:creator")
                    or item.findtext("author")
                    or feed_title  # use feed name as publisher fallback
                ).strip()

                published_at: str | None = None
                if pub_text:
                    try:
                        dt = datetime.strptime(
                            pub_text, "%a, %d %b %Y %H:%M:%S %z"
                        )
                        published_at = dt.isoformat()
                    except ValueError:
                        try:
                            dt = datetime.fromisoformat(
                                pub_text.replace("Z", "+00:00")
                            )
                            published_at = dt.isoformat()
                        except ValueError:
                            pass

                if link:
                    articles.append({
                        "title": title,
                        "url": link,
                        "source": self.source_name,
                        "published_at": published_at,
                        "content": description,
                        "publisher": author,
                    })
            except Exception:
                continue

        return articles
