"""FetcherFollowBuilders — follow-builders curated X feed (v1.0.41 / sprint-21).

Upstream: a centrally-curated, daily-updated JSON file hosting hand-picked
AI-builder tweets (26 builders / 6 podcasts / Anthropic-Claude blogs). This
fetcher only ingests the ``feed-x.json`` slice (X/Twitter tweets).

Design intent (dad 2026-09-02 locked):
  * experimental source: ``enabled: false`` in sources.yaml, seeded
    ``is_active=0`` — user flips it on in /config before it fetches.
  * bypass both keyword AND tag gates so English tweets (which would
    otherwise fail an active global keyword whitelist like ``银行/支付``
    or a tag-keyword gate) still land in the DB, then synthesize an
    ``ai-fintech`` tag for downstream brief/share.
  * cross-source soft dedupe: normalize the tweet URL (x.com/twitter.com,
    strip query), check a GLOBAL (any-source) EXISTS before insert so
    bestblog-x / xwatch duplicates don't double-store.  The physical
    ``UNIQUE(source, url)`` constraint is left untouched.

No API key, pure stdlib + httpx.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any

import httpx

from ..base import BaseFetcher, register_fetcher

logger = logging.getLogger(__name__)

FEED_URL = (
    "https://raw.githubusercontent.com/"
    "zarazhangrui/follow-builders/main/feed-x.json"
)

_DEFAULTS: dict[str, Any] = {
    "bypass_keyword_filters": True,
    "timeout_sec": 60,
    "http_timeout": 15.0,
    "skip_existing_urls": True,
}

#: Matches the numeric tweet id inside an /status/<id> X URL
_STATUS_RE = re.compile(r"/status/(\d+)")

#: Twitter/X host variants to normalize into the canonical https://x.com
_HOST_ALIASES = ("twitter.com", "mobile.twitter.com", "www.x.com", "www.twitter.com", "x.com")


def normalize_tweet_url(raw_url: str, handle: str = "", tweet_id: str = "") -> str:
    """Normalize an X/Twitter URL into ``https://x.com/<handle>/status/<id>``.

    Cross-source dedupe friendly: bestblog-x / xwatch may record the same
    tweet as ``https://twitter.com/foo/status/123?s=20`` or
    ``http://mobile.twitter.com/foo/status/123``.  We canonicalize by:
      1. extracting the numeric tweet id from the URL or an explicit id
      2. if found → rebuild ``https://x.com/{handle or '_'}/status/{id}``
      3. else → strip query/fragment and rewrite host aliases to x.com.
    """
    if not raw_url:
        raw_url = ""

    # 1. explicit tweet id + handle resolution
    id_m = _STATUS_RE.search(raw_url)
    resolved_id = (id_m.group(1) if id_m else None) or tweet_id

    # 2. strip query + fragment first so host-alias rewrite is clean
    base = raw_url.split("?")[0].split("#")[0]

    # 3. extract handle from the URL path (/<handle>/status/<id>) when present,
    #    falling back to the explicit ``handle`` arg.  This matters for
    #    cross-source dedupe: bestblog-x / xwatch URLs carry the handle.
    if not handle:
        m = re.search(r"/status/\d+", base)
        if m:
            path = base[: m.start()]
            seg = path.rstrip("/").rsplit("/", 1)[-1]
            if seg and seg not in _HOST_ALIASES and seg != "status":
                handle = seg

    if resolved_id:
        handle_seg = (handle or "_").strip().lstrip("@")
        # keep it safe for a URL path segment
        handle_seg = re.sub(r"[^A-Za-z0-9_]", "_", handle_seg) or "_"
        return f"https://x.com/{handle_seg}/status/{resolved_id}"

    # 3. no id — just normalize the host + drop query/fragment
    for host in _HOST_ALIASES:
        if base.startswith(f"http://{host}"):
            return "https://x.com" + base[len(f"http://{host}"):]
        if base.startswith(f"https://{host}"):
            return "https://x.com" + base[len(f"https://{host}"):]
    return base


def _truncate_title(text: str, handle: str, limit: int = 40) -> str:
    """Build a compact article title from tweet text.

    Strategy (tech-spec §3.2): first non-empty line, truncated to ``limit``
    chars with a ``...`` suffix; empty text falls back to ``@{handle} on X``.
    """
    text = (text or "").strip()
    if not text:
        return f"@{handle or 'x'} on X"
    first_line = text.splitlines()[0].strip()
    if len(first_line) > limit:
        return first_line[: limit - 3].rstrip() + "..."
    return first_line


@register_fetcher
class FetcherFollowBuilders(BaseFetcher):
    source_name: str = "follow_builders"

    # ── config ────────────────────────────────────────────────────────────
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
                    for k, v in user.items():
                        if k in _DEFAULTS:
                            cfg[k] = v
        except Exception as exc:  # noqa: BLE001
            logger.debug("[follow_builders] read fetch_config failed: %s", exc)
        return cfg

    # ── global (cross-source) url check ───────────────────────────────────
    @staticmethod
    def _is_url_exists_globally(canonical_url: str, tweet_id: str) -> bool:
        """True if any source already stored this tweet (by canonical url or tweet id)."""
        try:
            from ...db.connection import get_db
            conn = get_db()
            row = conn.execute(
                """
                SELECT 1 FROM articles
                WHERE url = ? OR url LIKE ?
                LIMIT 1
                """,
                (canonical_url, f"%status/{tweet_id}%"),
            ).fetchone()
            return row is not None
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "[follow_builders] global url-exists check failed for %s: %s",
                canonical_url, exc,
            )
            return False

    # ── fetch / parse ─────────────────────────────────────────────────────
    def fetch(self) -> list[dict[str, Any]]:
        """GET feed-x.json and return a flat list of raw tweet dicts."""
        cfg = self._load_config()
        http_timeout = float(cfg.get("http_timeout") or 15.0)

        try:
            resp = httpx.get(FEED_URL, timeout=http_timeout, follow_redirects=True)
            resp.raise_for_status()
            payload = resp.json()
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            logger.error("[follow_builders] failed to fetch/parse feed: %s", exc)
            return []

        if not isinstance(payload, dict):
            logger.error("[follow_builders] unexpected payload type: %s", type(payload).__name__)
            return []

        raws: list[dict[str, Any]] = []
        for builder in payload.get("x", []):
            if not isinstance(builder, dict):
                continue
            handle = builder.get("handle") or builder.get("name") or ""
            for tw in builder.get("tweets") or []:
                if not isinstance(tw, dict):
                    continue
                tw["_builder"] = {
                    "name": builder.get("name") or "",
                    "handle": handle,
                }
                raws.append(tw)
        return raws

    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        builder = raw.get("_builder") or {}
        name = builder.get("name") or ""
        handle = builder.get("handle") or ""
        text = raw.get("text") or ""
        raw_url = raw.get("url") or ""
        tweet_id = str(raw.get("id") or "")

        if not raw_url and not tweet_id:
            logger.debug("[follow_builders] tweet missing url+id — skipping")
            return None

        canonical_url = normalize_tweet_url(raw_url, handle=handle, tweet_id=tweet_id)

        # cross-source soft dedupe: run on every raw, so an already-stored
        # tweet from bestblog-x / xwatch never double-lands regardless of
        # which URL variant the upstream carried.
        if self._is_url_exists_globally(canonical_url, tweet_id):
            return None

        # published_at — ISO8601; tolerate a trailing 'Z' (naive → utc)
        published_at: str | None = raw.get("createdAt")
        if published_at:
            try:
                dt = datetime.fromisoformat(published_at.replace("Z", "+00:00"))
                published_at = dt.isoformat()
            except ValueError:
                logger.warning(
                    "[follow_builders] unparseable createdAt %r — null", published_at,
                )
                published_at = None

        content = text
        meta = []
        if raw.get("likes") is not None:
            meta.append(f"\U00002764 {raw['likes']}")
        if raw.get("retweets") is not None:
            meta.append(f"\U0001F501 {raw['retweets']}")
        if raw.get("replies") is not None:
            meta.append(f"\U0001F4AC {raw['replies']}")
        if meta:
            content = f"{text}\n\n[{(' | '.join(meta))}]"

        return {
            "title": _truncate_title(text, handle),
            "url": canonical_url,
            "source": self.source_name,
            "published_at": published_at,
            "content": content or text,
            "publisher": f"{name} (@{handle})" if name or handle else None,
        }