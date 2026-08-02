"""FetcherXWatch — X / Twitter keyword-driven discovery (v1.0.11).

Two-stage pipeline::

    sources.fetch_config.queries  (list[str])
        ↓
    FetcherXWatch.fetch()
        ↓ search_tweet_urls(query)   # DDG HTML interface → tweet URLs
        ↓ fetch_tweet(tweet_id)       # twitter syndication JSON → dict
    raw = {"data": <synd dict>, "url": <DDG-found URL>}
        ↓
    parse()  → tweet_to_article()  → canonical article dict
        ↓
    BaseFetcher.run()  ⇒ existing keyword filter + dedupe_insert pipeline

The fetcher is registered via ``@register_fetcher`` below — the
import side-effect in ``moni/fetch/sources/__init__.py`` triggers it.
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..base import BaseFetcher, register_fetcher
from ..xwatch_discovery import search_tweet_urls
from ..xwatch_syndication import fetch_tweet, tweet_id_from_url, tweet_to_article

logger = logging.getLogger(__name__)

# Defaults — used when ``sources.fetch_config`` is missing or missing keys.
# Queries here are illustrative; production values come from the seed.
_DEFAULTS = {
    "queries": [
        "微信小程序 触达",
        "微信小程序 服务通知",
        "微信小程序 提醒",
    ],
    "max_results_per_query": 20,
    # v1.0.11.A: bumped default from 2.5s → 5s after observing IP-level
    # throttling on DuckDuckGo's free endpoints.  A higher per-query
    # delay keeps us under their soft rate cap (anomaly page trips on
    # rapid-fire queries from the same IP).
    "request_delay": 5.0,
    "search_timeout": 20,
    "syndication_timeout": 20,
}


@register_fetcher
class FetcherXWatch(BaseFetcher):
    source_name: str = "xwatch"

    def fetch(self) -> list[dict[str, Any]]:
        """Discover + fetch raw tweet objects across all configured queries.

        Returns a list of raw dicts of shape::

            {"data": <syndication dict or None>, "url": <discovered URL>}

        Items where ``data`` is None are filtered out later in ``parse``;
        we keep them here so the dedup counter (``seen_ids``) is still
        updated to avoid hitting the same tweet twice in a single run.
        """
        cfg = self._load_config()
        queries = cfg.get("queries", [])
        if not isinstance(queries, list) or not queries:
            logger.warning(
                "[xwatch] no queries configured (fetch_config.queries "
                "missing or empty) — nothing to fetch",
            )
            return []

        # Coerce each entry to str, drop empties silently (the alternative
        # is to explode on a malformed row, which serves nobody).
        queries = [q for q in (str(q) for q in queries) if q.strip()]
        if not queries:
            return []

        max_results = int(cfg["max_results_per_query"])
        search_timeout = int(cfg["search_timeout"])
        synd_timeout = int(cfg["syndication_timeout"])
        delay = float(cfg["request_delay"])

        total = len(queries)
        # First progress tick: announce the total upfront so the UI can
        # show a 0/N counter immediately.
        if self.progress_callback is not None:
            try:
                self.progress_callback(
                    total_feeds=total,
                    done_feeds=0,
                    current_feed=None,
                    articles_so_far=0,
                )
            except Exception:
                pass

        seen_ids: set[str] = set()
        raws: list[dict[str, Any]] = []

        for i, query in enumerate(queries):
            if i > 0 and delay > 0:
                # Throttle so we don't trip DDG's anomaly challenge and
                # don't hammer twitter syndication past its soft limit.
                time.sleep(delay)

            try:
                urls = search_tweet_urls(
                    query, max_results=max_results, timeout=search_timeout,
                )
            except Exception as exc:
                # search_tweet_urls already swallows HTTP errors, but
                # guard against any unforeseen exceptions so one bad
                # query doesn't kill the whole run.
                logger.warning("[xwatch] discovery failed for %r: %s", query, exc)
                urls = []

            for url in urls:
                tid = tweet_id_from_url(url)
                if not tid or tid in seen_ids:
                    continue
                seen_ids.add(tid)

                try:
                    data = fetch_tweet(tid, timeout=synd_timeout)
                except Exception as exc:
                    logger.warning(
                        "[xwatch] syndication failed for %s: %s", tid, exc,
                    )
                    data = None

                raws.append({"data": data, "url": url})

            if self.progress_callback is not None:
                try:
                    self.progress_callback(
                        total_feeds=total,
                        done_feeds=i + 1,
                        current_feed=query,
                        articles_so_far=len(raws),
                    )
                except Exception:
                    pass

        logger.info(
            "[xwatch] done — %d queries, %d raw articles (%d unique tweet ids)",
            len(queries), len(raws), len(seen_ids),
        )
        return raws

    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        """Normalise a single raw item into the canonical article dict.

        ``raw["data"]`` may be None when syndication failed (deleted /
        withheld tweet) — those are dropped silently here.
        """
        data = raw.get("data")
        if not data:
            return None
        return tweet_to_article(
            data,
            source=self.source_name,
            fallback_url=raw.get("url"),
        )

    # ── internals ──────────────────────────────────────────────────────

    def _load_config(self) -> dict[str, Any]:
        """Read ``sources.fetch_config`` JSON, merging with module defaults.

        Mirrors ``FetcherBestBlogs._load_config`` so the user can tweak
        queries / limits via the existing ``/api/config/sources/{id}/fetch_config``
        endpoint without code changes.  On any error (table missing,
        JSON malformed), we fall back to ``_DEFAULTS``.
        """
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
                if isinstance(user, dict):
                    cfg.update({k: v for k, v in user.items() if k in _DEFAULTS})
        except Exception as exc:
            logger.debug("[xwatch] failed to read fetch_config: %s", exc)
        return cfg
