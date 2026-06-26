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

v1.0.9.4: 改用 GET /resources/{id} 端点（文档化端点），
  从 RSS <guid> 提取 resourceId，不再依赖不存在的 /search 端点。
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_BASE_URL = "https://api.bestblogs.dev/openapi/v2"
_TIMEOUT_RESOURCE = 15.0


def _get_api_key() -> str | None:
    """Read API key from environment."""
    return os.environ.get("BESTBLOGS_API_KEY") or None


def _headers(api_key: str) -> dict[str, str]:
    return {"X-API-KEY": api_key}


# ── Public API ─────────────────────────────────────────────────────


def fetch_resource(resource_id: str, api_key: str) -> dict[str, Any] | None:
    """Fetch article metadata from BestBlogs by resourceId.

    Uses GET /openapi/v2/resources/{id} (documented endpoint).
    Returns dict with keys: bb_score, bb_summary, bb_one_sentence,
    bb_main_points, bb_key_quotes, bb_tags, bb_resource_id.
    Returns None on error.

    Return value semantics:
    - dict   → success, metadata fetched
    - None   → error (transient: timeout, 5xx, 429; or permanent: 404)
    """
    resource_url = f"{_BASE_URL}/resources/{resource_id}"
    try:
        resp = httpx.get(
            resource_url,
            headers=_headers(api_key),
            timeout=_TIMEOUT_RESOURCE,
        )
        if resp.status_code == 429:
            logger.warning("[bestblogs_api] rate limited on resource %s", resource_id)
            return None
        if resp.status_code == 404:
            logger.warning("[bestblogs_api] resource %s not found (404)", resource_id)
            return None
        if resp.status_code != 200:
            logger.warning("[bestblogs_api] resource returned %d for %s", resp.status_code, resource_id)
            return None
        data = resp.json()
        if not data.get("success"):
            logger.warning("[bestblogs_api] resource failed: %s", data.get("message"))
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
        logger.warning("[bestblogs_api] resource timeout for %s", resource_id)
        return None
    except Exception as exc:
        logger.warning("[bestblogs_api] resource error for %s: %s", resource_id, exc)
        return None


def fetch_and_store(article_id: str) -> dict[str, Any] | None:
    """Fetch BestBlogs metadata for an article and write to DB.

    v1.0.9.4: 使用 RSS <guid> 提取的 bb_resource_id 直接调用
    GET /resources/{id}，不再依赖不存在的 /search 端点。

    1. Look up article from DB (source, bb_resource_id, bb_fetched_at)
    2. Skip if source != 'bestblogs*'
    3. Skip if bb_fetched_at already set (already fetched)
    4. Skip if bb_resource_id is NULL (no resourceId from RSS)
    5. Fetch resource metadata → write bb_* fields to DB
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
        "SELECT id, url, source, bb_resource_id, bb_fetched_at FROM articles WHERE id = ?",
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

    # v1.0.9.4: 使用 RSS <guid> 提取的 bb_resource_id
    resource_id = row["bb_resource_id"]
    if not resource_id:
        logger.info("[bestblogs_api] article %s has no bb_resource_id (RSS <guid> missing), skipping", article_id)
        # Mark fetched_at so we don't keep retrying
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE articles SET bb_fetched_at = ? WHERE id = ?",
            (now, article_id),
        )
        conn.commit()
        return None

    # Fetch resource metadata
    meta = fetch_resource(resource_id, api_key)
    if not meta:
        # Don't mark fetched_at on API error (allow retry)
        return None

    # Write to DB
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
