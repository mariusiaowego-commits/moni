"""BestBlogs OpenAPI v2 client — 按需拉取文章增强元数据.

触发时机：用户分享 bestblogs 源文章时，按需调用 API 获取：
  - score (0-100) 综合质量分
  - summary 详细摘要
  - oneSentenceSummary 一句话摘要
  - mainPoints [{point, explanation}] 要点列表
  - keyQuotes ["quote1", ...] 金句
  - tags ["AI Agent", ...] BestBlogs 标签（≠ moni tag）

API Key 从环境变量 BESTBLOGS_API_KEY 读取。
Free 用户每天 50 次调用额度。
"""
from __future__ import annotations

import json
import logging
import os
import urllib.parse
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.bestblogs.dev/openapi/v2"
_TIMEOUT_SEARCH = 20.0
_TIMEOUT_META = 15.0


def _get_api_key() -> str | None:
    """Read API key from environment."""
    return os.environ.get("BESTBLOGS_API_KEY") or None


def _headers(api_key: str) -> dict[str, str]:
    return {"X-API-KEY": api_key}


# ── Public API ─────────────────────────────────────────────────────


def search_by_url(url: str, api_key: str) -> str | None:
    """Search BestBlogs by article URL, return resourceId or None.

    Uses the /search endpoint with the URL as query.
    Returns the first match's resourceId, or None if no match.

    Return value semantics:
    - resourceId str  → found, proceed to fetch_meta
    - "" (empty str) → permanent miss (HTTP 200 but success=false) or
      HTTP 4xx (404 not indexed, 401/403 auth issue) — caller should
      mark bb_fetched_at to avoid retrying
    - None            → transient error (timeout, SSL EOF, HTTP 5xx,
      HTTP 429 rate-limit) — caller should NOT mark bb_fetched_at so
      we can retry on the next share/run
    """
    encoded = urllib.parse.quote(url, safe="")
    search_url = f"{_BASE_URL}/search?q={encoded}&pageSize=1"
    try:
        resp = httpx.get(
            search_url,
            headers=_headers(api_key),
            timeout=_TIMEOUT_SEARCH,
        )
        if resp.status_code == 429:
            # v1.0.9.2: rate-limit is transient.  Return None so caller
            # doesn't mark fetched_at; we'll retry on next run.
            logger.warning("[bestblogs_api] rate limited on search")
            return None
        if resp.status_code in (401, 403, 404):
            # Permanent: bad key / forbidden / URL not in index.
            # Mark fetched_at so we don't keep retrying with the same
            # bad input.
            logger.warning("[bestblogs_api] search HTTP %d (permanent)", resp.status_code)
            return ""
        if resp.status_code != 200:
            # Transient (5xx, etc.) — return None so we retry next time.
            logger.warning("[bestblogs_api] search HTTP %d (transient)", resp.status_code)
            return None
        data = resp.json()
        if not data.get("success"):
            # API-level failure message — likely permanent for this URL.
            logger.warning("[bestblogs_api] search failed: %s", data.get("message"))
            return ""
        items = data.get("data", {}).get("dataList", [])
        if not items:
            # 200 OK but empty list — URL genuinely not in their index.
            logger.info("[bestblogs_api] search no results for URL: %s", url[:80])
            return ""
        resource_id = items[0].get("resourceId")
        if not resource_id:
            logger.info("[bestblogs_api] search hit but no resourceId for URL: %s", url[:80])
            return ""
        logger.info("[bestblogs_api] found %s for URL", resource_id)
        return resource_id
    except httpx.TimeoutException:
        logger.warning("[bestblogs_api] search timeout for URL: %s", url[:80])
        return None  # transient
    except (httpx.ConnectError, httpx.RemoteProtocolError) as exc:
        # v1.0.9.2: SSL EOF / connection reset / TLS handshake failures
        # are transient — return None so caller retries next run instead
        # of marking bb_fetched_at permanently.
        logger.warning("[bestblogs_api] search connection error (transient): %s", exc)
        return None
    except json.JSONDecodeError as exc:
        # v1.0.9.3: resp.json() on empty body / non-JSON 200 response.
        # Common when BestBlogs upstream SSL EOFs or returns 200 with
        # empty body.  Explicit catch so the log message names the cause
        # (the generic `except Exception` below is the safety net).
        logger.warning(
            "[bestblogs_api] search returned non-JSON body (transient): %s",
            exc,
        )
        return None
    except Exception as exc:
        # Unknown exception — treat as transient to be safe.
        logger.warning("[bestblogs_api] search error (treated transient): %s", exc)
        return None


def fetch_meta(resource_id: str, api_key: str) -> dict[str, Any] | None:
    """Fetch article metadata from BestBlogs by resourceId.

    Returns dict with keys: score, summary, oneSentenceSummary,
    mainPoints, keyQuotes, tags.
    Returns None on error.
    """
    meta_url = f"{_BASE_URL}/resources/{resource_id}/meta"
    try:
        resp = httpx.get(
            meta_url,
            headers=_headers(api_key),
            timeout=_TIMEOUT_META,
        )
        if resp.status_code == 429:
            logger.warning("[bestblogs_api] rate limited on meta for %s", resource_id)
            return None
        if resp.status_code != 200:
            logger.warning("[bestblogs_api] meta returned %d for %s", resp.status_code, resource_id)
            return None
        data = resp.json()
        if not data.get("success"):
            logger.warning("[bestblogs_api] meta failed: %s", data.get("message"))
            return None
        d = data.get("data", {})
        return {
            "bb_score": d.get("score"),
            "bb_summary": d.get("summary"),
            "bb_one_sentence": d.get("oneSentenceSummary"),
            "bb_main_points": d.get("mainPoints"),
            "bb_key_quotes": d.get("keyQuotes"),
            "bb_tags": d.get("tags"),
            "bb_resource_id": resource_id,
        }
    except httpx.TimeoutException:
        logger.warning("[bestblogs_api] meta timeout for %s", resource_id)
        return None
    except Exception as exc:
        logger.warning("[bestblogs_api] meta error for %s: %s", resource_id, exc)
        return None


def fetch_and_store(article_id: str) -> dict[str, Any] | None:
    """Fetch BestBlogs metadata for an article and write to DB.

    1. Look up article from DB (url, source, bb_fetched_at)
    2. Skip if source != 'bestblogs'
    3. Skip if bb_fetched_at already set (already fetched)
    4. Search BestBlogs by URL → resourceId
    5. Fetch meta → write bb_* fields to DB
    6. Mark bb_fetched_at

    Returns the fetched metadata dict, or None if skipped/failed.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.info("[bestblogs_api] no BESTBLOGS_API_KEY, skipping")
        return None

    from ..db.connection import get_db

    conn = get_db()
    row = conn.execute(
        "SELECT id, url, source, bb_fetched_at FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        logger.warning("[bestblogs_api] article %s not found", article_id)
        return None

    # v1.0.9.3: was `row["source"] != "bestblogs"` (string equality).
    # This excluded the 3 bestblog-* sub-sources (bestblog-podcast /
    # bestblog-x / bestblog-yt) introduced in v1.0.5.18, leaving 156
    # articles unable to get BB enhancement.  Use a prefix check.
    if not (row["source"] == "bestblogs" or row["source"].startswith("bestblog-")):
        logger.debug("[bestblogs_api] article %s source=%s, not bestblogs*", article_id, row["source"])
        return None

    if row["bb_fetched_at"]:
        logger.info("[bestblogs_api] article %s already fetched at %s", article_id, row["bb_fetched_at"])
        # Return existing data
        existing = conn.execute(
            "SELECT bb_score, bb_summary, bb_one_sentence, bb_main_points, "
            "bb_key_quotes, bb_tags, bb_resource_id FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        if existing and existing["bb_score"] is not None:
            return dict(existing)
        return None

    # Step 1: Search by URL.  Returns:
    #   str        → resourceId found, proceed
    #   ""         → permanent miss (URL not in BestBlogs index), mark fetched
    #   None       → transient error (SSL EOF, timeout, 5xx, 429), leave for retry
    resource_id = search_by_url(row["url"], api_key)
    if resource_id is None:
        # Transient: do NOT mark bb_fetched_at so the next share will
        # retry.  The caller (worker / CLI) treats None from
        # fetch_and_store as "skip this round, will try again".
        logger.info("[bestblogs_api] transient error for %s, will retry next run", article_id)
        return None
    if resource_id == "":
        # Permanent: URL not in BestBlogs index.  Mark so we don't
        # keep searching for the same URL forever.
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE articles SET bb_fetched_at = ? WHERE id = ?",
            (now, article_id),
        )
        conn.commit()
        logger.info("[bestblogs_api] article %s not found on BestBlogs, marked fetched", article_id)
        return None

    # Step 2: Fetch meta
    meta = fetch_meta(resource_id, api_key)
    if not meta:
        # Don't mark fetched_at on API error (allow retry)
        return None

    # Step 3: Write to DB
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE articles SET
            bb_score = ?,
            bb_summary = ?,
            bb_one_sentence = ?,
            bb_main_points = ?,
            bb_key_quotes = ?,
            bb_tags = ?,
            bb_resource_id = ?,
            bb_fetched_at = ?
        WHERE id = ?
        """,
        (
            meta.get("bb_score"),
            meta.get("bb_summary"),
            meta.get("bb_one_sentence"),
            json.dumps(meta.get("bb_main_points"), ensure_ascii=False) if meta.get("bb_main_points") else None,
            json.dumps(meta.get("bb_key_quotes"), ensure_ascii=False) if meta.get("bb_key_quotes") else None,
            json.dumps(meta.get("bb_tags"), ensure_ascii=False) if meta.get("bb_tags") else None,
            meta.get("bb_resource_id"),
            now,
            article_id,
        ),
    )
    conn.commit()
    logger.info(
        "[bestblogs_api] stored meta for article %s: score=%s, resource=%s",
        article_id, meta.get("bb_score"), meta.get("bb_resource_id"),
    )
    return meta
