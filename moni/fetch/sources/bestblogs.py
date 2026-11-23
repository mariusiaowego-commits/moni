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
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import re

import httpx

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

def _clean_description(text: str) -> str:
    """Strip WeChat boilerplate from RSS description."""
    from ..content import clean_boilerplate

    cleaned = clean_boilerplate(text)
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
    "max_feeds": 500,
    "max_articles_per_feed": 0,   # <= 0 means "every item the feed returns"
    "max_articles_total": 200000,  # safety valve, not a working limit
    "request_delay": 0.5,
    "flush_every_feeds": 10,
}

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_OVERRIDES_PATH = _PROJECT_ROOT / "config" / "feed_overrides.json"


def _load_known_urls(source_name: str) -> set[str]:
    """URLs already stored under ``source_name``.

    Used to skip the tag gate for articles we already have.  Keyed by
    ``(source, url)`` — the articles table's unique constraint — so the
    same URL published by another source is still evaluated here.
    Basket rows are deliberately *not* included: a keyword added later
    must be able to pull a previously dropped article in.
    """
    try:
        from ...db.connection import get_db

        conn = get_db()
        return {
            row[0]
            for row in conn.execute(
                "SELECT url FROM articles WHERE source = ? AND url IS NOT NULL",
                (source_name,),
            )
        }
    except Exception as exc:  # noqa: BLE001 - dedup is best-effort
        logger.debug("[bestblogs] known-url preload failed: %s", exc)
        return set()


def _load_feed_overrides(source_name: str, path: Path | None = None) -> dict[str, list[dict]]:
    """Per-source feed 取舍（exclude / extra），见 ``config/feed_overrides.json``.

    文件缺失或损坏时按「无覆盖」处理 —— 抓取不能因为一个可选配置文件而失败。
    """
    path = path or _OVERRIDES_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        logger.warning("[%s] %s not found — no feed overrides", source_name, path)
        return {"exclude_feeds": [], "extra_feeds": []}
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("[%s] ignoring bad %s: %s", source_name, path, exc)
        return {"exclude_feeds": [], "extra_feeds": []}

    block = data.get(source_name) if isinstance(data, dict) else None
    if not isinstance(block, dict):
        return {"exclude_feeds": [], "extra_feeds": []}

    def _entries(key: str) -> list[dict]:
        raw = block.get(key)
        if not isinstance(raw, list):
            return []
        return [
            e for e in raw
            if isinstance(e, dict) and isinstance(e.get("url"), str) and e["url"]
        ]

    return {"exclude_feeds": _entries("exclude_feeds"), "extra_feeds": _entries("extra_feeds")}


def _apply_feed_overrides(
    feeds: list[tuple[str, str]],
    overrides: dict[str, list[dict]],
    log_name: str = "bestblogs",
) -> list[tuple[str, str]]:
    """先放 extra（保证在 max_feeds 截断前跑到），再按 URL 排除 exclude。"""
    exclude = {e["url"]: e.get("name") or e["url"] for e in overrides["exclude_feeds"]}
    present = {url for _, url in feeds}
    matched = [name for url, name in exclude.items() if url in present]
    missing = [name for url, name in exclude.items() if url not in present]
    if missing:
        logger.warning(
            "[%s] %d excluded feed(s) not in OPML (已失效的删除项): %s",
            log_name, len(missing), ", ".join(missing[:5]),
        )

    merged: list[tuple[str, str]] = []
    seen: set[str] = set()
    extra = [(e.get("name") or e["url"], e["url"]) for e in overrides["extra_feeds"]]
    for title, url in extra + feeds:
        if url in seen or url in exclude:
            continue
        seen.add(url)
        merged.append((title, url))

    logger.info(
        "[%s] feed overrides: +%d extra, -%d/%d excluded matched, %d feeds total",
        log_name, len(extra), len(matched), len(exclude), len(merged),
    )
    return merged


@register_fetcher
class FetcherBestBlogs(BaseFetcher):
    source_name: str = "bestblogs"
    run_stats: dict[str, Any] = {}
    _last_feed_failed: bool = False

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

        # dad 圈定的 feed 取舍（config/feed_overrides.json），按 source_name 分块。
        # bestblog-yt / bestblog-podcast 继承本类但读各自的块（通常为空）；
        # bestblog-x 有自己的 fetch()，不读这个配置。
        overrides = _load_feed_overrides(self.source_name)
        if overrides["exclude_feeds"] or overrides["extra_feeds"]:
            feeds = _apply_feed_overrides(feeds, overrides, self.source_name)

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

        # 2. Fetch each feed, flush to the DB every ``flush_every_feeds``.
        # A 375-feed sweep takes minutes; keeping everything in memory meant a
        # process restart discarded every article fetched so far.
        buffer: list[dict[str, Any]] = []
        collected = 0
        delay = config["request_delay"]
        max_per_feed = config["max_articles_per_feed"]
        max_total = config["max_articles_total"]
        flush_every = config["flush_every_feeds"]
        known_urls = _load_known_urls(self.source_name)
        seen_this_run: set[str] = set()
        stats = {
            "feeds_total": len(feeds),
            "feeds_scanned": 0,
            "feeds_failed": 0,
            "candidates_new": 0,
            "selected": 0,
            "budget_capped": False,
        }

        for i, (feed_title, feed_url) in enumerate(feeds):
            if max_total > 0 and collected >= max_total:
                stats["budget_capped"] = True
                logger.info(
                    "[bestblogs] reached total cap %d, stopping at feed %d/%d",
                    max_total, i, len(feeds),
                )
                break

            remaining = max_total - collected if max_total > 0 else None
            if i > 0 and delay > 0:
                time.sleep(delay)

            items = self._fetch_feed(feed_url, feed_title)
            stats["feeds_scanned"] = i + 1
            if getattr(self, "_last_feed_failed", False):
                stats["feeds_failed"] += 1
            # 0 (or negative) = take every item the feed returned.
            if max_per_feed > 0:
                items = items[:max_per_feed]

            fresh: list[dict[str, Any]] = []
            for article in items:
                url = article.get("url")
                if not url or url in known_urls or url in seen_this_run:
                    continue
                seen_this_run.add(url)
                fresh.append(article)
            stats["candidates_new"] += len(fresh)

            if remaining is not None:
                fresh = fresh[:remaining]
            buffer.extend(fresh)
            collected += len(fresh)
            stats["selected"] = collected

            # v1.0.7.6: per-feed progress callback (current_feed + counters).
            if self.progress_callback is not None:
                try:
                    self.progress_callback(
                        done_feeds=i + 1,
                        current_feed=feed_title,
                        articles_so_far=collected,
                    )
                except Exception:
                    pass

            if flush_every > 0 and (i + 1) % flush_every == 0:
                self._flush(buffer)
                buffer = []

            if (i + 1) % 10 == 0:
                logger.info(
                    "[bestblogs] fetched %d/%d feeds, %d articles so far",
                    i + 1, len(feeds), collected,
                )

        self._flush(buffer)
        self.run_stats = stats

        logger.info(
            "[bestblogs] done — %d/%d feeds, %d new candidates, %d selected",
            stats["feeds_scanned"], len(feeds), stats["candidates_new"], collected,
        )
        # Everything was persisted in chunks already.
        return []

    def _flush(self, buffer: list[dict[str, Any]]) -> None:
        """Apply the date filter, then run the shared pipeline on one chunk."""
        if not buffer:
            return
        articles = buffer
        if self.date_from or self.date_to:
            before = len(articles)
            articles = [
                a for a in articles
                if _in_date_range(a.get("published_at"), self.date_from, self.date_to)
            ]
            logger.info(
                "[bestblogs] date filter: %d → %d articles (from=%s, to=%s)",
                before, len(articles), self.date_from, self.date_to,
            )
        if articles:
            self.ingest_chunk(articles)

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

        v1.0.34.1: send a browser-like User-Agent and retry transient
        transport errors.  wechat2rss.bestblogs.dev rejects plain
        ``httpx`` requests (default python-httpx UA) with an abrupt
        ``SSL: UNEXPECTED_EOF_WHILE_READING`` mid-download ~40% of the
        time during a full 375-feed sweep; a browser UA + one retry
        brings success to ~100% (verified against 财新/钛媒体/券商中国/
        AIBase基地/华尔街见闻).
        """
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/126.0.0.0 Safari/537.36"
            )
        }
        self._last_feed_failed = False
        resp: httpx.Response | None = None
        for attempt in (1, 2, 3):
            try:
                resp = httpx.get(feed_url, timeout=20.0, headers=headers)
                if resp.status_code == 404:
                    logger.debug("[bestblogs] 404: %s", feed_title)
                    self._last_feed_failed = True
                    return []
                resp.raise_for_status()
                break
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                logger.warning(
                    "[bestblogs] fetch %s attempt %d/3 failed (%s: %s)",
                    feed_title, attempt, type(exc).__name__, exc,
                )
                if attempt == 3:
                    logger.warning(
                        "[bestblogs] giving up on %s after 3 attempts", feed_title
                    )
                    self._last_feed_failed = True
                    return []
                time.sleep(1.5 * attempt)
            except Exception as exc:
                logger.warning("[bestblogs] error fetching %s: %s", feed_title, exc)
                self._last_feed_failed = True
                return []
        # resp is guaranteed bound here: the loop either broke on success
        # or returned early on all failure paths.
        assert resp is not None

        try:
            root = ET.fromstring(resp.text.encode("utf-8"))
        except Exception:
            self._last_feed_failed = True
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
                    # v1.0.9.12: store raw GUID even without RAW_ prefix so
                    # fetch_and_store() can try it; the API returns 404 for
                    # non-BestBlogs GUIDs, which is safe.
                    guid = (item.findtext("guid") or "").strip()
                    bb_resource_id = guid if guid else None

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
