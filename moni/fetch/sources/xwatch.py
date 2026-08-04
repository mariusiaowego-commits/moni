"""FetcherXWatch — three-channel X discovery (v1.0.12).

Channels (all feed into syndication-normalised articles + tag filter)::

    Channel C — following (browser CDP, most stable when logged in)
        Profile 6 / port 9222 → scroll home?following=true
        Config: following_enabled, following_max_posts, following_max_scrolls

    Channel A — accounts (RSS via bestblogs_x.opml / account_rss)
        DDG from:handle only as fallback

    Channel B — keywords (DDG Lite, circuit-breaker + rotation)

Rotation (A/B)
--------------
* ``accounts_per_run`` (default 5)
* ``queries_per_run``  (default 1)
Salt = prior fetch_jobs count for xwatch (else hour-of-epoch).
"""
from __future__ import annotations

import json
import logging
import time
from typing import Any

from ..base import BaseFetcher, register_fetcher
from ..xwatch_accounts import fetch_account_tweet_urls, normalize_handle
from ..xwatch_discovery import search_tweet_urls
from ..xwatch_following import (
    following_tweet_to_synd_dict,
    scrape_following,
)
from ..xwatch_syndication import fetch_tweet, tweet_id_from_url, tweet_to_article

logger = logging.getLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    # Channel C — Following timeline (CDP / Profile 6)
    "following_enabled": True,
    "following_max_posts": 100,
    "following_max_scrolls": 40,
    "following_scroll_pause": 1.8,
    # Channel B — keyword discovery (DDG)
    "queries": [
        "微信小程序 触达",
        "微信小程序 服务通知",
        "微信小程序 提醒",
    ],
    "max_results_per_query": 10,
    "queries_per_run": 1,
    "request_delay": 8.0,
    "search_timeout": 20,
    "syndication_timeout": 20,
    "ddg_circuit_minutes": 60.0,
    # Channel A — account watchlist
    "accounts": [
        "op7418",
        "imxiaohu",
        "vista8",
        "PMbackttfuture",
        "oran_ge",
        "lijigang_com",
        "HiTw93",
        "hidecloud",
        "geekbb",
        "idoubicc",
    ],
    "accounts_per_run": 5,
    "max_results_per_account": 5,
    "account_rss": {},
    "account_ddg_fallback": True,
    "skip_existing_urls": True,
    # When following already has full text, skip syndication HTTP
    "following_skip_syndication": True,
}


def _rotate_slice(items: list[Any], *, n: int, salt: int) -> list[Any]:
    if not items or n <= 0:
        return []
    if n >= len(items):
        return list(items)
    start = salt % len(items)
    return [items[(start + i) % len(items)] for i in range(n)]


def _rotation_salt() -> int:
    try:
        from ...db.connection import get_db
        row = get_db().execute(
            "SELECT COUNT(*) AS n FROM fetch_jobs WHERE source = 'xwatch'",
        ).fetchone()
        if row is not None:
            return int(row["n"])
    except Exception:
        pass
    return int(time.time() // 3600)


@register_fetcher
class FetcherXWatch(BaseFetcher):
    source_name: str = "xwatch"

    def fetch(self) -> list[dict[str, Any]]:
        """Discover tweets via C→A→B, return raws for parse()."""
        cfg = self._load_config()
        accounts = self._coerce_str_list(cfg.get("accounts"))
        queries = self._coerce_str_list(cfg.get("queries"))
        following_on = bool(cfg.get("following_enabled", True))

        if not following_on and not accounts and not queries:
            logger.warning(
                "[xwatch] no channels enabled (following/accounts/queries empty)",
            )
            return []

        salt = _rotation_salt()
        accounts_n = max(0, int(cfg.get("accounts_per_run") or 0))
        queries_n = max(0, int(cfg.get("queries_per_run") or 0))
        selected_accounts = _rotate_slice(accounts, n=accounts_n, salt=salt)
        selected_queries = _rotate_slice(queries, n=queries_n, salt=salt)

        max_per_account = int(cfg["max_results_per_account"])
        max_per_query = int(cfg["max_results_per_query"])
        search_timeout = int(cfg["search_timeout"])
        synd_timeout = int(cfg["syndication_timeout"])
        delay = float(cfg["request_delay"])
        circuit_minutes = float(cfg.get("ddg_circuit_minutes") or 60.0)
        account_rss = cfg.get("account_rss") if isinstance(cfg.get("account_rss"), dict) else {}
        allow_ddg_fb = bool(cfg.get("account_ddg_fallback", True))
        skip_existing = bool(cfg.get("skip_existing_urls", True))
        skip_synd_following = bool(cfg.get("following_skip_syndication", True))

        # Progress: following counts as 1 step + accounts + queries
        steps: list[tuple[str, str]] = []
        if following_on:
            steps.append(("following", "following"))
        steps.extend(("account", a) for a in selected_accounts)
        steps.extend(("query", q) for q in selected_queries)
        total = len(steps) or 1

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
        stats = {
            "following_posts": 0,
            "accounts_selected": len(selected_accounts),
            "queries_selected": len(selected_queries),
            "account_rss_hits": 0,
            "account_ddg_hits": 0,
            "query_urls": 0,
            "synd_ok": 0,
            "synd_fail": 0,
            "skipped_existing": 0,
        }

        for i, (kind, label) in enumerate(steps):
            if i > 0 and delay > 0 and kind != "following":
                # following is one block; delay only between HTTP-heavy steps
                time.sleep(delay)

            if kind == "following":
                try:
                    tweets = scrape_following(
                        max_posts=int(cfg.get("following_max_posts") or 100),
                        max_scrolls=int(cfg.get("following_max_scrolls") or 40),
                        scroll_pause=float(cfg.get("following_scroll_pause") or 1.8),
                        ensure=True,
                        stop_chrome=False,
                    )
                except Exception as exc:
                    logger.warning("[xwatch] following scrape failed: %s", exc)
                    tweets = []
                stats["following_posts"] = len(tweets)
                for tw in tweets:
                    url = tw.get("url") or ""
                    tid = tw.get("id_str") or tweet_id_from_url(url)
                    if not tid or tid in seen_ids:
                        continue
                    seen_ids.add(tid)
                    if skip_existing and url and self._url_exists(url):
                        stats["skipped_existing"] += 1
                        continue
                    data = following_tweet_to_synd_dict(tw)
                    if data and skip_synd_following:
                        stats["synd_ok"] += 1
                        raws.append({"data": data, "url": url, "via": "following:cdp"})
                        continue
                    # Optional syndication enrichment
                    try:
                        synd = fetch_tweet(str(tid), timeout=synd_timeout)
                    except Exception:
                        synd = None
                    if synd:
                        stats["synd_ok"] += 1
                        raws.append({"data": synd, "url": url, "via": "following:synd"})
                    elif data:
                        stats["synd_ok"] += 1
                        raws.append({"data": data, "url": url, "via": "following:cdp"})
                    else:
                        stats["synd_fail"] += 1
                        raws.append({"data": None, "url": url, "via": "following:fail"})

            elif kind == "account":
                try:
                    urls, channel = fetch_account_tweet_urls(
                        label,
                        max_results=max_per_account,
                        timeout=search_timeout,
                        account_rss=account_rss,
                        allow_ddg_fallback=allow_ddg_fb,
                        ddg_search=lambda q, **kw: search_tweet_urls(
                            q,
                            max_results=kw.get("max_results", max_per_account),
                            timeout=kw.get("timeout", search_timeout),
                            circuit_minutes=circuit_minutes,
                        ),
                    )
                    if channel == "rss":
                        stats["account_rss_hits"] += 1
                    elif channel == "ddg":
                        stats["account_ddg_hits"] += 1
                    via = f"account:{channel}"
                except Exception as exc:
                    logger.warning("[xwatch] account @%s failed: %s", label, exc)
                    urls, via = [], "account:err"
                self._ingest_urls(
                    urls, via=via, seen_ids=seen_ids, raws=raws, stats=stats,
                    skip_existing=skip_existing, synd_timeout=synd_timeout,
                )

            else:  # query
                try:
                    urls = search_tweet_urls(
                        label,
                        max_results=max_per_query,
                        timeout=search_timeout,
                        circuit_minutes=circuit_minutes,
                    )
                    stats["query_urls"] += len(urls)
                    via = "query:ddg"
                except Exception as exc:
                    logger.warning("[xwatch] discovery failed for %r: %s", label, exc)
                    urls, via = [], "query:err"
                self._ingest_urls(
                    urls, via=via, seen_ids=seen_ids, raws=raws, stats=stats,
                    skip_existing=skip_existing, synd_timeout=synd_timeout,
                )

            if self.progress_callback is not None:
                try:
                    self.progress_callback(
                        total_feeds=total,
                        done_feeds=i + 1,
                        current_feed=f"{kind}:{label}",
                        articles_so_far=len(raws),
                    )
                except Exception:
                    pass

        logger.info(
            "[xwatch] done — following=%d accounts=%d/%d queries=%d/%d raw=%d "
            "unique_ids=%d rss=%d acct_ddg=%d query_urls=%d synd_ok=%d synd_fail=%d skip=%d",
            stats["following_posts"],
            len(selected_accounts), len(accounts),
            len(selected_queries), len(queries),
            len(raws), len(seen_ids),
            stats["account_rss_hits"], stats["account_ddg_hits"],
            stats["query_urls"], stats["synd_ok"], stats["synd_fail"],
            stats["skipped_existing"],
        )
        self._last_fetch_stats = stats  # type: ignore[attr-defined]
        return raws

    def _ingest_urls(
        self,
        urls: list[str],
        *,
        via: str,
        seen_ids: set[str],
        raws: list[dict[str, Any]],
        stats: dict[str, Any],
        skip_existing: bool,
        synd_timeout: int,
    ) -> None:
        for url in urls:
            tid = tweet_id_from_url(url)
            if not tid or tid in seen_ids:
                continue
            seen_ids.add(tid)
            if skip_existing and self._url_exists(url):
                stats["skipped_existing"] += 1
                continue
            try:
                data = fetch_tweet(tid, timeout=synd_timeout)
            except Exception as exc:
                logger.warning("[xwatch] syndication failed for %s: %s", tid, exc)
                data = None
            if data:
                stats["synd_ok"] += 1
            else:
                stats["synd_fail"] += 1
            raws.append({"data": data, "url": url, "via": via})

    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        data = raw.get("data")
        if not data:
            return None
        return tweet_to_article(
            data,
            source=self.source_name,
            fallback_url=raw.get("url"),
        )

    @staticmethod
    def _coerce_str_list(value: Any) -> list[str]:
        if not isinstance(value, list):
            return []
        out: list[str] = []
        for item in value:
            if item is None:
                continue
            s = str(item).strip()
            if s:
                out.append(s)
        return out

    def _url_exists(self, url: str) -> bool:
        try:
            from ...db.connection import get_db
            row = get_db().execute(
                "SELECT 1 FROM articles WHERE source = ? AND url = ? LIMIT 1",
                (self.source_name, url),
            ).fetchone()
            return row is not None
        except Exception:
            return False

    def _load_config(self) -> dict[str, Any]:
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
                    for k, v in user.items():
                        if k in _DEFAULTS:
                            cfg[k] = v
        except Exception as exc:
            logger.debug("[xwatch] failed to read fetch_config: %s", exc)
        return cfg
