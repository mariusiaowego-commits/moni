"""FetcherWechatRSS — WeChat articles via WeChatRSS aggregation endpoint.

v1.0.5.6: split into multiple sources (wechat_rss_a, wechat_rss_b, ...)
to support multiple free-tier WeChatRSS accounts.  Each account's
token now lives directly in the source's ``url`` field (full RSS URL
including ``?token=...``), not in an env var.  Rationale:

* The free tier of waytomaster limits each account to tracking 2
  public accounts.  Multiple accounts = more public accounts covered.
* Putting the token in the source URL keeps the config self-contained
  and visible in the management UI (vs. a hidden env var).
* The previous env-var path is kept as a fallback so existing
  ``WECHAT_RSS_TOKEN`` setups still work — but new code should
  always pass the full URL.
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any
from xml.etree import ElementTree as ET

import httpx

from ..base import BaseFetcher, list_fetchers, register_fetcher

logger = logging.getLogger(__name__)

# Fallback URL used when the source's url field is empty AND the env
# var is set.  Matches the v1.0.4-eval convention.
DEFAULT_FEED_URL = "https://wechatrss.waytomaster.com/api/rss/all"

# Marker for "the env-var / WECHAT_RSS_TOKEN path".  We import the
# constant here so linters don't choke on the missing real value.
_ENV_TOKEN_NAME = "WECHAT_RSS_TOKEN"


def _load_token_from_envfile() -> str:
    """Fallback: read WECHAT_RSS_TOKEN from .env.

    v1.0.4: moni is a single-user local project; web service sources
    .env via ``scripts/web.sh`` but ``python3 -m moni fetch`` does not.
    This fallback lets the fetcher self-load the token without forcing
    the user to export it every time.

    v1.0.5.6: still useful as a last-resort fallback when source.url
    is empty (e.g. legacy sources.yaml entries that pre-date the URL
    embedding convention).  New sources should embed the token in url.
    """
    from pathlib import Path

    env_path = Path(__file__).parent.parent.parent.parent / ".env"
    if not env_path.exists():
        return ""
    try:
        for raw in env_path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith(_ENV_TOKEN_NAME + "="):
                value = line.split("=", 1)[1].strip()
                if value.startswith(("'", '"')) and value.endswith(("'", '"')):
                    value = value[1:-1]
                return value
    except Exception as exc:
        logger.warning("[wechat_rss] Failed to read .env: %s", exc)
    return ""


@register_fetcher
class FetcherWechatRSS(BaseFetcher):
    # v1.0.5.6: a single class backs wechat_rss_a / wechat_rss_b / etc.
    # The actual ``source_name`` used for dedupe is the per-row value
    # pulled from sources.url, not this class attribute, so there's no
    # risk of cross-contamination.  The class attribute is kept for
    # backward compatibility (legacy ``--source wechat_rss`` CLI calls
    # that don't go through the DB row dispatch).
    source_name: str = "wechat_rss"

    def __init__(self, source_name: str | None = None) -> None:
        """Allow callers to override ``source_name`` for multi-account setups.

        The fetcher registry instantiates classes with no arguments, so
        the override only kicks in when a caller (typically the test
        suite or a future ad-hoc dispatcher) wants to point the same
        class at a different DB row.  In production we set
        ``source_name`` via the per-variant subclass trick below.
        """
        if source_name is not None:
            self.source_name = source_name
        super().__init__()

    def fetch(self) -> list[dict[str, Any]]:
        feed_url = self._resolve_url()
        if not feed_url:
            logger.error(
                "[%s] no feed URL available — set source.url (with ?token=...) "
                "or WECHAT_RSS_TOKEN env var",
                self.source_name,
            )
            return []

        try:
            resp = httpx.get(feed_url, timeout=15.0)
            if resp.status_code == 404:
                logger.warning(
                    "[%s] Feed not found (404) — check token in url",
                    self.source_name,
                )
                return []
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            logger.error(
                "[%s] HTTP %s fetching %s",
                self.source_name, exc.response.status_code, exc.request.url,
            )
            return []
        except httpx.TimeoutException:
            logger.error("[%s] Timeout fetching %s", self.source_name, feed_url)
            return []
        except Exception as exc:
            logger.error(
                "[%s] Unexpected error fetching %s: %s",
                self.source_name, feed_url, exc,
            )
            return []

        try:
            root = ET.fromstring(resp.text.encode("utf-8"))
        except Exception as exc:
            logger.error("[%s] Failed to parse RSS XML: %s", self.source_name, exc)
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

                # Extract author from <dc:creator> or <author>
                author = (
                    item.findtext("dc:creator")
                    or item.findtext("author")
                    or ""
                ).strip()

                published_at: str | None = None
                if pub_text:
                    try:
                        dt = datetime.strptime(pub_text, "%a, %d %b %Y %H:%M:%S %z")
                        published_at = dt.isoformat()
                    except ValueError:
                        try:
                            dt = datetime.fromisoformat(pub_text.replace("Z", "+00:00"))
                            published_at = dt.isoformat()
                        except ValueError:
                            logger.warning(
                                "[%s] Unparseable pubDate %r — skipping",
                                self.source_name, pub_text,
                            )

                articles.append({
                    "title": title,
                    "url": link,
                    "source": self.source_name,
                    "published_at": published_at,
                    "content": description,
                    "publisher": author,
                })
            except Exception as exc:
                logger.warning(
                    "[%s] Error parsing RSS item: %s — skipping",
                    self.source_name, exc,
                )

        return articles

    def _resolve_url(self) -> str:
        """Figure out which feed URL to hit for this fetcher instance.

        v1.0.5.6: priority order is
        1. ``sources.url`` row for our source_name (full URL with token)
        2. ``WECHAT_RSS_TOKEN`` env var + DEFAULT_FEED_URL (legacy)
        3. ``.env`` file (also legacy)

        Returns empty string if nothing's set — caller treats that as
        "no feed, log error, return []".
        """
        # Path 1: look up our source's url from the DB.
        try:
            from ...db.connection import get_db
            with get_db() as conn:
                row = conn.execute(
                    "SELECT url FROM sources WHERE name = ?",
                    (self.source_name,),
                ).fetchone()
                if row and row["url"]:
                    return row["url"]
        except Exception as exc:
            logger.debug(
                "[%s] DB lookup for source url failed: %s",
                self.source_name, exc,
            )

        # Path 2/3: legacy env var fallback.
        token = os.environ.get(_ENV_TOKEN_NAME, "") or _load_token_from_envfile()
        if token:
            return f"{DEFAULT_FEED_URL}?token={token}"
        return ""

    def parse(self, raw: dict[str, Any]) -> dict[str, Any] | None:
        if not raw.get("url"):
            return None
        # v1.0.5.6: always stamp the per-instance source_name (wechat_rss_a,
        # wechat_rss_b, etc.) on the article dict.  Without this, the
        # dedupe key in articles.url would collide across accounts
        # because raw["source"] is whatever the caller passed in.
        return {
            "title": raw.get("title") or "",
            "url": raw.get("url"),
            "source": self.source_name,
            "published_at": raw.get("published_at"),
            "content": raw.get("content") or "",
            "publisher": raw.get("publisher"),
        }


# ── v1.0.5.6: alias registration for multi-account support ────────────
# waytomaster's free tier allows 2 public accounts per token.  To cover
# more accounts we maintain N independent tokens, each represented as
# a distinct source_name in DB / sources.yaml.  All of them use the
# same fetcher code; only the per-row ``url`` (and therefore the
# ``source_name`` we stamp on each article) differs.
#
# We register one subclass per variant so the global fetcher
# registry's key-based lookup works without any other code change.
# Each subclass shares ``FetcherWechatRSS`` code; the only difference
# is the ``source_name`` class attribute, which the registry keys on.

_WECHAT_RSS_VARIANTS = (
    "wechat_rss_a",
    "wechat_rss_b",
    "wechat_rss_c",
    "wechat_rss_d",
    "wechat_rss_e",
)


def _register_aliases() -> None:
    """Create and register one fetcher class per ``wechat_rss_*`` variant.

    The dynamic ``type()`` call produces a subclass whose only
    override is the ``source_name`` class attribute.  Calling
    ``register_fetcher`` (a no-op if the key is already taken) makes
    each variant discoverable via ``list_fetchers()`` and
    ``moni fetch --source wechat_rss_a``.
    """
    for variant in _WECHAT_RSS_VARIANTS:
        # Avoid re-registering if the module is reloaded — the
        # registry would otherwise warn about duplicate keys.
        existing = list_fetchers().get(variant)
        if existing is not None:
            continue
        cls = type(variant, (FetcherWechatRSS,), {"source_name": variant})
        register_fetcher(cls)


_register_aliases()
