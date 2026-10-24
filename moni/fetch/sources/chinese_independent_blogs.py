"""FetcherChineseIndependentBlogs — Chinese independent blog OPML aggregate.

Upstream: https://github.com/geekplux/chinese-independent-blogs
License: MIT

The upstream list is a static OPML file containing hundreds of
independently operated blogs.  Unlike BestBlogs, it is not a curated
news feed and its entries are unrelated to moni's banking/payment
taxonomy.  This fetcher therefore:

* resolves the OPML from a remote URL or a repo-local ``config/`` file;
* scans a bounded, rotating window of feeds per run;
* follows neither redirects outside http(s) nor non-http(s) feed URLs;
* parses both RSS 2.0 and Atom 1.0;
* reports per-run feed health so a stale upstream entry cannot abort
  the whole source.

Configuration lives in ``sources.fetch_config``::

    {
        "max_feeds": 80,
        "max_articles_per_feed": 3,
        "max_articles_total": 240,
        "request_delay": 0.35,
        "http_timeout": 12.0,
        "min_feeds_before_failure_rate_check": 10,
        "max_failure_rate": 0.7,
    }
"""
from __future__ import annotations

import json
import logging
import math
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_LOCAL_CONFIG_ROOT = (_PROJECT_ROOT / "config").resolve()
_MAX_RESPONSE_BYTES = 5 * 1024 * 1024
_ATOM_NS = "{http://www.w3.org/2005/Atom}"
_DEFAULT_OPML = "https://raw.githubusercontent.com/geekplux/chinese-independent-blogs/master/feed.opml"

_DEFAULTS = {
    "max_feeds": 80,
    "max_articles_per_feed": 3,
    "max_articles_total": 240,
    "request_delay": 0.35,
    "http_timeout": 12.0,
    "min_feeds_before_failure_rate_check": 10,
    "max_failure_rate": 0.7,
}

_USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)


def _is_http_url(value: str) -> bool:
    return value.startswith("http://") or value.startswith("https://")


def _positive_int(
    value: Any,
    default: int,
    minimum: int = 1,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool):
        return default
    try:
        parsed = int(value)
    except (TypeError, ValueError, OverflowError):
        return default
    parsed = max(minimum, parsed)
    if maximum is not None:
        parsed = min(maximum, parsed)
    return parsed


def _bounded_float(value: Any, default: float, low: float, high: float) -> float:
    if isinstance(value, bool):
        return default
    try:
        parsed = float(value)
    except (TypeError, ValueError, OverflowError):
        return default
    if not math.isfinite(parsed):
        return default
    return min(high, max(low, parsed))


def _validate_config(cfg: dict[str, Any]) -> dict[str, Any]:
    """Coerce user-edited JSON values into safe runtime values."""
    out = dict(cfg)
    out["max_feeds"] = _positive_int(
        cfg.get("max_feeds"), _DEFAULTS["max_feeds"], maximum=10_000
    )
    out["max_articles_per_feed"] = _positive_int(
        cfg.get("max_articles_per_feed"),
        _DEFAULTS["max_articles_per_feed"],
        maximum=1_000,
    )
    out["max_articles_total"] = _positive_int(
        cfg.get("max_articles_total"),
        _DEFAULTS["max_articles_total"],
        maximum=100_000,
    )
    out["request_delay"] = _bounded_float(
        cfg.get("request_delay"), _DEFAULTS["request_delay"], 0.0, 60.0
    )
    out["http_timeout"] = _bounded_float(
        cfg.get("http_timeout"), _DEFAULTS["http_timeout"], 1.0, 120.0
    )
    out["min_feeds_before_failure_rate_check"] = _positive_int(
        cfg.get("min_feeds_before_failure_rate_check"),
        _DEFAULTS["min_feeds_before_failure_rate_check"],
        maximum=10_000,
    )
    out["max_failure_rate"] = _bounded_float(
        cfg.get("max_failure_rate"), _DEFAULTS["max_failure_rate"], 0.0, 1.0
    )
    if out["min_feeds_before_failure_rate_check"] > out["max_feeds"]:
        logger.warning(
            "[cn-indie] min_feeds_before_failure_rate_check (%d) > max_feeds (%d); "
            "clamping to max_feeds",
            out["min_feeds_before_failure_rate_check"], out["max_feeds"],
        )
        out["min_feeds_before_failure_rate_check"] = out["max_feeds"]
    return out


def _inside_config_root(path: Path) -> bool:
    try:
        path.resolve().relative_to(_LOCAL_CONFIG_ROOT)
        return True
    except ValueError:
        return False


def _parse_date(raw: str | None) -> str | None:
    if not raw:
        return None
    text = raw.strip()
    try:
        return datetime.strptime(text, "%a, %d %b %Y %H:%M:%S %z").isoformat()
    except ValueError:
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).isoformat()
    except ValueError:
        return None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1].lower()


def _text(element: ET.Element | None) -> str:
    if element is None:
        return ""
    return "".join(element.itertext()).strip()


@register_fetcher
class FetcherChineseIndependentBlogs(BaseFetcher):
    source_name: str = "chinese_independent_blogs"

    def fetch(self) -> list[dict[str, Any]]:
        self.last_stats = {
            "feeds_total": 0,
            "feeds_scanned": 0,
            "feeds_ok": 0,
            "feeds_empty": 0,
            "feeds_invalid": 0,
            "feeds_failed": 0,
            "feeds_http_error": 0,
            "cursor_start": 0,
            "cursor_next": 0,
        }
        config = self._load_config()
        opml_urls = self._resolve_opml_urls()
        if not opml_urls:
            logger.error("[cn-indie] no OPML URL/path configured")
            return []

        feeds: list[tuple[str, str]] = []
        seen: set[str] = set()
        for opml_url in opml_urls:
            for title, feed_url in self._load_opml(opml_url):
                if feed_url in seen:
                    continue
                seen.add(feed_url)
                feeds.append((title, feed_url))

        if not feeds:
            logger.warning("[cn-indie] OPML contained no usable feeds")
            return []

        start = self._load_cursor() % len(feeds)
        rotated = feeds[start:] + feeds[:start]
        window = rotated[: config["max_feeds"]]

        logger.info(
            "[cn-indie] OPML %d feeds, scanning %d from offset %d",
            len(feeds), len(window), start,
        )

        articles: list[dict[str, Any]] = []
        feeds_ok = feeds_empty = feeds_failed = feeds_invalid = 0
        feeds_http_error = 0
        scanned = 0

        for index, (feed_title, feed_url) in enumerate(window):
            if len(articles) >= config["max_articles_total"]:
                break
            if index and config["request_delay"] > 0:
                time.sleep(config["request_delay"])

            scanned += 1
            status, items = self._fetch_feed(
                feed_url, feed_title, timeout=config["http_timeout"]
            )

            if status == "ok":
                feeds_ok += 1
                articles.extend(items[: config["max_articles_per_feed"]])
            elif status == "empty":
                feeds_empty += 1
            elif status == "invalid":
                feeds_invalid += 1
            elif status == "http_error":
                feeds_http_error += 1
            else:
                feeds_failed += 1

            if self.progress_callback is not None:
                try:
                    self.progress_callback(
                        total_feeds=len(window),
                        done_feeds=index + 1,
                        current_feed=feed_title,
                        articles_so_far=len(articles),
                    )
                except Exception:
                    pass

            # A few dead feeds are normal in this long-tail OPML: 389 of
            # 695 entries were unreachable in the 2026-10-24 sweep.  Stop
            # only when the whole run looks network-broken, not after an
            # arbitrary consecutive streak.
            min_checked = config["min_feeds_before_failure_rate_check"]
            if (
                scanned >= min_checked
                and feeds_failed / scanned > config["max_failure_rate"]
            ):
                logger.warning(
                    "[cn-indie] stopping: %d/%d transport failures (%.0f%%) "
                    "exceeds %.0f%%",
                    feeds_failed, scanned,
                    feeds_failed / scanned * 100,
                    config["max_failure_rate"] * 100,
                )
                break

        next_cursor = (start + scanned) % len(feeds)
        self._save_cursor(next_cursor)

        articles = articles[: config["max_articles_total"]]
        self.last_stats.update({
            "feeds_total": len(feeds),
            "feeds_scanned": scanned,
            "feeds_ok": feeds_ok,
            "feeds_empty": feeds_empty,
            "feeds_invalid": feeds_invalid,
            "feeds_failed": feeds_failed,
            "feeds_http_error": feeds_http_error,
            "cursor_start": start,
            "cursor_next": next_cursor,
        })
        logger.info(
            "[cn-indie] done — scanned %d, ok %d, empty %d, invalid %d, "
            "failed %d, http_error %d, articles %d, next cursor %d",
            scanned, feeds_ok, feeds_empty, feeds_invalid, feeds_failed,
            feeds_http_error,
            len(articles), next_cursor,
        )
        return articles

    def run(self, dry_run: bool = False) -> dict[str, Any]:
        result = super().run(dry_run=dry_run)
        result.update(getattr(self, "last_stats", {}))
        return result

    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        url = (raw.get("url") or "").strip()
        if not _is_http_url(url):
            return None
        return {
            "title": (raw.get("title") or "").strip(),
            "url": url,
            "source": self.source_name,
            "published_at": raw.get("published_at"),
            "content": raw.get("content") or "",
            "publisher": raw.get("publisher"),
        }

    # -- config / cursor -------------------------------------------------

    def _load_config(self) -> dict[str, Any]:
        cfg = dict(_DEFAULTS)
        try:
            from ...db.connection import get_db

            row = get_db().execute(
                "SELECT fetch_config FROM sources WHERE name = ?",
                (self.source_name,),
            ).fetchone()
            if row and row["fetch_config"]:
                user = json.loads(row["fetch_config"])
                if isinstance(user, dict):
                    cfg.update({k: v for k, v in user.items() if k in _DEFAULTS})
        except Exception as exc:
            logger.debug("[cn-indie] failed to read fetch_config: %s", exc)
        return _validate_config(cfg)

    def _resolve_opml_urls(self) -> list[str]:
        try:
            from ...db.connection import get_db

            row = get_db().execute(
                "SELECT url FROM sources WHERE name = ?",
                (self.source_name,),
            ).fetchone()
            if row and row["url"]:
                return [part.strip() for part in row["url"].split(",") if part.strip()]
        except Exception as exc:
            logger.debug("[cn-indie] DB lookup failed: %s", exc)
        return [_DEFAULT_OPML]

    def _cursor_path(self) -> Path:
        import os

        data_dir = Path(os.environ.get("MONI_DATA_DIR", "data"))
        return data_dir / ".cn_indie_cursor.json"

    def _load_cursor(self) -> int:
        path = self._cursor_path()
        try:
            value = json.loads(path.read_text(encoding="utf-8")).get("offset", 0)
            return max(0, int(value))
        except Exception:
            return 0

    def _save_cursor(self, offset: int) -> None:
        path = self._cursor_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_suffix(".tmp")
            tmp.write_text(json.dumps({"offset": offset}), encoding="utf-8")
            tmp.replace(path)
        except Exception as exc:
            logger.debug("[cn-indie] failed to persist cursor: %s", exc)

    # -- OPML ------------------------------------------------------------

    def _load_opml(self, location: str) -> list[tuple[str, str]]:
        try:
            if _is_http_url(location):
                text = self._http_text(location, timeout=30.0)
            else:
                path = Path(location)
                if not path.is_absolute():
                    path = _PROJECT_ROOT / path
                if not _inside_config_root(path):
                    logger.error("[cn-indie] local OPML outside config/: %s", location)
                    return []
                text = path.read_text(encoding="utf-8")
        except Exception as exc:
            logger.error("[cn-indie] failed to load OPML %s: %s", location, exc)
            return []

        try:
            root = ET.fromstring(text.encode("utf-8"))
        except Exception as exc:
            logger.error("[cn-indie] invalid OPML %s: %s", location, exc)
            return []

        feeds: list[tuple[str, str]] = []
        for outline in root.iter("outline"):
            feed_url = (outline.get("xmlUrl") or "").strip()
            if not feed_url:
                continue
            if not _is_http_url(feed_url):
                logger.warning("[cn-indie] skipped non-http feed: %s", feed_url[:100])
                continue
            title = (outline.get("title") or outline.get("text") or feed_url).strip()
            feeds.append((title, feed_url))
        logger.info("[cn-indie] parsed %d feeds from %s", len(feeds), location)
        return feeds

    # -- HTTP ------------------------------------------------------------

    def _http_text(self, url: str, timeout: float) -> str:
        headers = {"User-Agent": _USER_AGENT}
        with httpx.stream("GET", url, headers=headers, timeout=timeout,
                          follow_redirects=True) as resp:
            resp.raise_for_status()
            chunks: list[bytes] = []
            size = 0
            for chunk in resp.iter_bytes():
                size += len(chunk)
                if size > _MAX_RESPONSE_BYTES:
                    raise ValueError(f"response exceeds {_MAX_RESPONSE_BYTES} bytes")
                chunks.append(chunk)
            encoding = resp.encoding or "utf-8"
        return b"".join(chunks).decode(encoding, errors="replace")

    def _fetch_feed(
        self, feed_url: str, feed_title: str, *, timeout: float
    ) -> tuple[str, list[dict[str, Any]]]:
        if not _is_http_url(feed_url):
            return "invalid", []
        try:
            text = self._http_text(feed_url, timeout=timeout)
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            logger.debug("[cn-indie] HTTP %d %s", status_code, feed_title)
            # Permanent removals are not evidence that the network is
            # down; transient throttling/server errors are.
            if status_code == 429 or status_code >= 500:
                return "failed", []
            return "http_error", []
        except httpx.RequestError as exc:
            logger.debug("[cn-indie] transport %s: %s: %s",
                         feed_title, type(exc).__name__, exc)
            return "failed", []
        except Exception as exc:
            logger.debug("[cn-indie] failed %s: %s: %s",
                         feed_title, type(exc).__name__, exc)
            return "http_error", []

        try:
            root = ET.fromstring(text.encode("utf-8"))
        except Exception as exc:
            logger.debug("[cn-indie] invalid XML %s: %s", feed_title, exc)
            return "invalid", []

        if _local_name(root.tag) == "feed":
            items = self._parse_atom(root, feed_title)
        elif _local_name(root.tag) == "rss":
            items = self._parse_rss(root, feed_title)
        else:
            logger.debug("[cn-indie] unexpected XML root %s for %s",
                         root.tag, feed_title)
            return "invalid", []

        return ("ok" if items else "empty"), items

    # -- RSS / Atom ------------------------------------------------------

    def _parse_rss(self, root: ET.Element, feed_title: str) -> list[dict[str, Any]]:
        channel = root.find("channel")
        if channel is None:
            return []

        articles: list[dict[str, Any]] = []
        for item in channel.findall("item"):
            link = _text(item.find("link"))
            if not _is_http_url(link):
                continue
            author = _text(item.find("author")) or feed_title
            articles.append({
                "title": _text(item.find("title")),
                "url": link,
                "published_at": _parse_date(_text(item.find("pubDate"))),
                "content": _text(item.find("description")),
                "publisher": author,
            })
        return articles

    def _parse_atom(self, root: ET.Element, feed_title: str) -> list[dict[str, Any]]:
        articles: list[dict[str, Any]] = []
        for entry in root.findall(f"{_ATOM_NS}entry"):
            link = ""
            for candidate in entry.findall(f"{_ATOM_NS}link"):
                if (candidate.get("rel") or "alternate") == "alternate":
                    link = (candidate.get("href") or "").strip()
                    if link:
                        break
            if not _is_http_url(link):
                continue

            author = ""
            author_el = entry.find(f"{_ATOM_NS}author")
            if author_el is not None:
                author = _text(author_el.find(f"{_ATOM_NS}name"))

            content_el = entry.find(f"{_ATOM_NS}content")
            if content_el is None:
                content_el = entry.find(f"{_ATOM_NS}summary")

            articles.append({
                "title": _text(entry.find(f"{_ATOM_NS}title")),
                "url": link,
                "published_at": _parse_date(_text(entry.find(f"{_ATOM_NS}published")))
                or _parse_date(_text(entry.find(f"{_ATOM_NS}updated"))),
                "content": _text(content_el),
                "publisher": author or feed_title,
            })
        return articles
