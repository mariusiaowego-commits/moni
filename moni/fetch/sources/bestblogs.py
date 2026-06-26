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


def _in_date_range(
    pub_date: str | None, date_from: str | None, date_to: str | None
) -> bool:
    """Check if an article's published_at falls within [date_from, date_to].

    Args:
        pub_date: ISO datetime string from the article (e.g. "2026-06-12T07:45:00+08:00")
        date_from: ISO date string (e.g. "2026-06-10"), inclusive
        date_to: ISO date string (e.g. "2026-06-13"), inclusive (end of day)

    Returns:
        True if the article is within range, or if pub_date is missing (keep it).
    """
    if not pub_date:
        return True  # no date info — keep the article
    try:
        art_date = datetime.fromisoformat(pub_date.replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return True  # unparseable — keep the article

    if date_from:
        try:
            dt_from = datetime.fromisoformat(date_from + "T00:00:00+00:00")
            if art_date < dt_from:
                return False
        except ValueError:
            pass

    if date_to:
        try:
            dt_to = datetime.fromisoformat(date_to + "T23:59:59+00:00")
            if art_date > dt_to:
                return False
        except ValueError:
            pass

    return True

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
        opml_urls = self._resolve_urls()
        if not opml_urls:
            logger.error("[bestblogs] no OPML URL in sources.url")
            return []

        # 1. Fetch all OPMLs → merged list of (title, feed_url), deduped
        # Exclude x.com feeds (moved to bestblog-x source)
        feeds: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        for url in opml_urls:
            for title, feed_url in self._fetch_opml(url):
                if feed_url in seen_urls:
                    continue
                if "xgo.ing" in feed_url:
                    continue  # Skip x.com feeds (moved to bestblog-x)
                seen_urls.add(feed_url)
                feeds.append((title, feed_url))

        if not feeds:
            return []

        logger.info("[bestblogs] merged %d OPML(s) → %d unique feeds", len(opml_urls), len(feeds))

        max_feeds = config["max_feeds"]
        if len(feeds) > max_feeds:
            logger.info(
                "[bestblogs] OPML has %d feeds, limiting to %d",
                len(feeds), max_feeds,
            )
        feeds = feeds[:max_feeds]

        # v1.0.7.6: notify progress hook of total feed count up front.
        if self.progress_callback is not None:
            try:
                self.progress_callback(
                    total_feeds=len(feeds),
                    done_feeds=0,
                    articles_so_far=0,
                )
            except Exception:
                pass

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

            # v1.0.7.6: per-feed progress callback (current_feed + counters).
            if self.progress_callback is not None:
                try:
                    self.progress_callback(
                        done_feeds=i + 1,
                        current_feed=feed_title,
                        articles_so_far=len(all_articles),
                    )
                except Exception:
                    pass

            if (i + 1) % 10 == 0:
                logger.info(
                    "[bestblogs] fetched %d/%d feeds, %d articles so far",
                    i + 1, len(feeds), len(all_articles),
                )

        # 3. Hard cap
        if len(all_articles) > max_total:
            all_articles = all_articles[:max_total]

        # 4. Date-range filtering (v1.0.5.24)
        if self.date_from or self.date_to:
            before = len(all_articles)
            all_articles = [
                a for a in all_articles
                if _in_date_range(a.get("published_at"), self.date_from, self.date_to)
            ]
            logger.info(
                "[bestblogs] date filter: %d → %d articles (from=%s, to=%s)",
                before, len(all_articles), self.date_from, self.date_to,
            )

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

    def _resolve_urls(self) -> list[str]:
        """OPML URL(s) from sources.url — comma-separated → list."""
        try:
            from ...db.connection import get_db
            conn = get_db()
            row = conn.execute(
                "SELECT url FROM sources WHERE name = ?",
                (self.source_name,),
            ).fetchone()
            if row and row["url"]:
                return [u.strip() for u in row["url"].split(",") if u.strip()]
        except Exception as exc:
            logger.debug("[bestblogs] DB lookup failed: %s", exc)
        return []

    def _fetch_opml(self, opml_url: str) -> list[tuple[str, str]]:
        """Fetch OPML, return [(title, xmlUrl), ...].

        Supports both HTTP URLs and local file paths.
        """
        try:
            if opml_url.startswith("/") or opml_url.startswith("./"):
                # Local file
                with open(opml_url, "r", encoding="utf-8") as f:
                    xml_text = f.read()
            else:
                # HTTP URL
                resp = httpx.get(opml_url, timeout=30.0)
                resp.raise_for_status()
                xml_text = resp.text
        except Exception as exc:
            logger.error("[bestblogs] failed to fetch OPML: %s", exc)
            return []

        try:
            root = ET.fromstring(xml_text.encode("utf-8"))
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
        """Fetch one RSS feed, return article dicts.

        Supports both RSS 2.0 (<rss><channel><item>) and Atom
        (<feed xmlns=...Atom><entry>) — YouTube / GitHub / many podcast
        platforms publish Atom.  Detection is by root element name.
        """
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

        # Atom detection: root tag == {http://www.w3.org/2005/Atom}feed
        if root.tag.endswith("}feed") or root.tag == "feed":
            return self._parse_atom(root, feed_title)
        return self._parse_rss(root, feed_title)

    def _parse_rss(
        self, root: ET.Element, feed_title: str
    ) -> list[dict[str, Any]]:
        """Parse RSS 2.0 (<rss><channel><item>)."""
        channel = root.find("channel")
        items = channel.findall("item") if channel is not None else []
        return self._extract_items(
            items, feed_title, item_tag="item",
            title_path="title", link_path="link", desc_path="description",
            pub_tag="pubDate", author_fallback=feed_title,
        )

    def _parse_atom(
        self, root: ET.Element, feed_title: str
    ) -> list[dict[str, Any]]:
        """Parse Atom 1.0 (<feed xmlns=...Atom><entry>)."""
        ATOM_NS = "{http://www.w3.org/2005/Atom}"
        entries = root.findall(f"{ATOM_NS}entry")
        # Wrap each Atom <entry> in a synthesized <item> with simple child
        # elements so the generic _extract_items logic works uniformly.
        items: list[ET.Element] = []
        for entry in entries:
            # Atom <link rel="alternate"> holds the article URL.
            link_alt = None
            for link in entry.findall(f"{ATOM_NS}link"):
                rel = link.get("rel") or "alternate"
                if rel == "alternate" and link.get("href"):
                    link_alt = link.get("href")
                    break
            wrapper = ET.Element("item")
            # <title>
            title_el = entry.find(f"{ATOM_NS}title")
            wrapper_title = ET.SubElement(wrapper, "title")
            if title_el is not None and title_el.text:
                wrapper_title.text = title_el.text.strip()
            # <link>
            link_el = ET.SubElement(wrapper, "link")
            link_el.text = link_alt or ""
            # <published> → <pubDate>
            pub_el = entry.find(f"{ATOM_NS}published")
            if pub_el is not None and pub_el.text:
                ET.SubElement(wrapper, "pubDate").text = pub_el.text
            # <author><name>
            author_el = entry.find(f"{ATOM_NS}author")
            if author_el is not None:
                name_el = author_el.find(f"{ATOM_NS}name")
                if name_el is not None and name_el.text:
                    ET.SubElement(wrapper, "author").text = name_el.text.strip()
            # <summary> or <content> → <description> (best-effort snippet)
            desc_el = entry.find(f"{ATOM_NS}summary")
            if desc_el is None:
                desc_el = entry.find(f"{ATOM_NS}content")
            if desc_el is not None and desc_el.text:
                ET.SubElement(wrapper, "description").text = desc_el.text[:5000]
            items.append(wrapper)

        return self._extract_items(
            items, feed_title, item_tag="item",
            title_path="title", link_path="link", desc_path="description",
            pub_tag="pubDate", author_fallback=feed_title,
        )

    def _extract_items(
        self,
        items: list[ET.Element],
        feed_title: str,
        *,
        item_tag: str,
        title_path: str,
        link_path: str,
        desc_path: str,
        pub_tag: str,
        author_fallback: str,
    ) -> list[dict[str, Any]]:
        """Extract article dicts from a list of parsed <item>/<entry> elements."""
        articles: list[dict[str, Any]] = []
        for item in items:
            try:
                title = (item.findtext(title_path) or "").strip()
                link = (item.findtext(link_path) or "").strip()
                description = _clean_description(
                    (item.findtext(desc_path) or "").strip()
                )
                pub_text = item.findtext(pub_tag)

                author = (
                    item.findtext("dc:creator")
                    or item.findtext("author")
                    or author_fallback
                ).strip()

                published_at: str | None = None
                if pub_text:
                    # Try RFC 822 (RSS pubDate) first, then ISO 8601 (Atom published).
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
                    # Extract BestBlogs resourceId from <guid> if present
                    # (BestBlogs RSS feeds include <guid>RAW_xxxxx</guid>)
                    guid = (item.findtext("guid") or "").strip()
                    bb_resource_id = guid if guid.startswith("RAW_") else None

                    articles.append({
                        "title": title,
                        "url": link,
                        "source": self.source_name,
                        "published_at": published_at,
                        "content": description,
                        "publisher": author,
                        "bb_resource_id": bb_resource_id,
                    })
            except Exception:
                continue

        return articles


@register_fetcher
class FetcherBestBlogPodcast(FetcherBestBlogs):
    """BestBlogs podcast — reuses FetcherBestBlogs with different source_name."""
    source_name: str = "bestblog-podcast"


@register_fetcher
class FetcherBestBlogYt(FetcherBestBlogs):
    """BestBlogs YouTube — reuses FetcherBestBlogs with different source_name."""
    source_name: str = "bestblog-yt"


@register_fetcher
class FetcherBestBlogX(FetcherBestBlogs):
    """BestBlogs X (Twitter) — reuses FetcherBestBlogs with different source_name."""
    source_name: str = "bestblog-x"

    def fetch(self) -> list[dict[str, Any]]:
        config = self._load_config()
        opml_urls = self._resolve_urls()
        if not opml_urls:
            logger.error("[bestblog-x] no OPML URL in sources.url")
            return []

        # Fetch all OPMLs → merged list of (title, feed_url), deduped
        # DO NOT exclude xgo.ing feeds (this IS the x.com source)
        feeds: list[tuple[str, str]] = []
        seen_urls: set[str] = set()
        for url in opml_urls:
            for title, feed_url in self._fetch_opml(url):
                if feed_url in seen_urls:
                    continue
                seen_urls.add(feed_url)
                feeds.append((title, feed_url))

        if not feeds:
            return []

        logger.info("[bestblog-x] merged %d OPML(s) → %d unique feeds", len(opml_urls), len(feeds))

        max_feeds = config["max_feeds"]
        if len(feeds) > max_feeds:
            logger.info(
                "[bestblog-x] OPML has %d feeds, limiting to %d",
                len(feeds), max_feeds,
            )
        feeds = feeds[:max_feeds]

        # Fetch each feed, collect articles
        all_articles: list[dict[str, Any]] = []
        delay = config["request_delay"]
        max_per_feed = config["max_articles_per_feed"]
        max_total = config["max_articles_total"]

        for i, (feed_title, feed_url) in enumerate(feeds):
            if len(all_articles) >= max_total:
                logger.info(
                    "[bestblog-x] reached total cap %d, stopping at feed %d/%d",
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
                    "[bestblog-x] fetched %d/%d feeds, %d articles so far",
                    i + 1, len(feeds), len(all_articles),
                )

        # Hard cap
        if len(all_articles) > max_total:
            all_articles = all_articles[:max_total]

        # Date-range filtering
        if self.date_from or self.date_to:
            before = len(all_articles)
            all_articles = [
                a for a in all_articles
                if _in_date_range(a.get("published_at"), self.date_from, self.date_to)
            ]
            logger.info(
                "[bestblog-x] date filter: %d → %d articles (from=%s, to=%s)",
                before, len(all_articles), self.date_from, self.date_to,
            )

        logger.info(
            "[bestblog-x] done — %d feeds, %d articles",
            len(feeds), len(all_articles),
        )
        return all_articles
