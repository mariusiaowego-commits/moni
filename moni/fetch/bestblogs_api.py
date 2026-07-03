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

v1.0.9.7: 支持独立字段去重，每个 BB 字段有独立的 fetched_at 时间戳。
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

# BB field to DB column mapping
BB_FIELD_MAP = {
    "score": "bb_score",
    "summary": "bb_summary",
    "one_sentence": "bb_one_sentence",
    "main_points": "bb_main_points",
    "key_quotes": "bb_key_quotes",
    "tags": "bb_tags",
}

# BB field to fetched_at column mapping
BB_FETCHED_AT_MAP = {
    "score": "bb_score_fetched_at",
    "summary": "bb_fetched_at",  # summary uses the main bb_fetched_at
    "one_sentence": "bb_fetched_at",  # one_sentence uses the main bb_fetched_at
    "main_points": "bb_main_points_fetched_at",
    "key_quotes": "bb_key_quotes_fetched_at",
    "tags": "bb_tags_fetched_at",
}


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


def _needs_fetch(conn, article_id: str, fields: list[str]) -> bool:
    """Check if any of the requested fields need fetching.

    Returns True if at least one field has NULL fetched_at.
    """
    if not fields:
        return False
    
    # Get the fetched_at columns for the requested fields
    fetched_at_cols = []
    for field in fields:
        col = BB_FETCHED_AT_MAP.get(field)
        if col:
            fetched_at_cols.append(col)
    
    if not fetched_at_cols:
        return False
    
    # Check if any of the fetched_at columns are NULL
    # Also check the main bb_fetched_at as a fallback
    placeholders = ", ".join(fetched_at_cols)
    row = conn.execute(
        f"SELECT {placeholders} FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    
    if not row:
        return False
    
    # Return True if any fetched_at is NULL
    return any(v is None for v in row)


def fetch_and_store(article_id: str, fields: list[str] | None = None) -> dict[str, Any] | None:
    """Fetch BestBlogs metadata for an article and write to DB.

    v1.0.9.4: 使用 RSS <guid> 提取的 bb_resource_id 直接调用
    GET /resources/{id}，不再依赖不存在的 /search 端点。

    v1.0.9.7: 支持独立字段去重，每个 BB 字段有独立的 fetched_at 时间戳。
    如果 fields 参数指定，只更新指定的字段。

    1. Look up article from DB (source, bb_resource_id, fetched_at)
    2. Skip if source != 'bestblogs*'
    3. Skip if bb_resource_id is NULL (no resourceId from RSS)
    4. Check if any requested fields need fetching
    5. Fetch resource metadata → write requested bb_* fields to DB
    6. Mark fetched_at for updated fields

    Returns the fetched metadata dict, or None if skipped/failed.
    """
    api_key = _get_api_key()
    if not api_key:
        logger.info("[bestblogs_api] no BESTBLOGS_API_KEY, skipping")
        return None

    from ..db.connection import get_db

    conn = get_db()
    row = conn.execute(
        "SELECT id, url, source, bb_resource_id FROM articles WHERE id = ?",
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

    # v1.0.9.4: 使用 RSS <guid> 提取的 bb_resource_id
    resource_id = row["bb_resource_id"]
    if not resource_id:
        logger.info(
            "[bestblogs_api] article %s has no bb_resource_id (RSS <guid> missing), "
            "skipping — fetch resource after backfill or add URL-based lookup",
            article_id,
        )
        # v1.0.9.12: DO NOT mark fetched_at here — the article may get a
        # bb_resource_id later (backfill, improved fetcher).  Marking
        # fetched_at poisons the record permanently: _needs_fetch() sees
        # non-NULL and never retries.  Leave all NULL so the next
        # share-triggered fetch retries.
        return None

    # Default: fetch all fields
    if fields is None:
        fields = list(BB_FIELD_MAP.keys())

    # Check if any requested fields need fetching
    if not _needs_fetch(conn, article_id, fields):
        logger.info("[bestblogs_api] article %s already has all requested fields", article_id)
        # Return existing data
        existing = conn.execute(
            "SELECT bb_score, bb_summary, bb_one_sentence, bb_main_points, "
            "bb_key_quotes, bb_tags, bb_resource_id FROM articles WHERE id = ?",
            (article_id,),
        ).fetchone()
        if existing:
            return dict(existing)
        return None

    # Fetch resource metadata
    meta = fetch_resource(resource_id, api_key)
    if not meta:
        # Don't mark fetched_at on API error (allow retry)
        return None

    # Build UPDATE statement for requested fields only
    now = datetime.now(timezone.utc).isoformat()
    update_parts = []
    update_values = []
    
    for field in fields:
        db_col = BB_FIELD_MAP.get(field)
        fetched_at_col = BB_FETCHED_AT_MAP.get(field)
        
        if not db_col or not fetched_at_col:
            continue
        
        # Get the value from meta
        meta_key = f"bb_{field}"
        value = meta.get(meta_key)
        
        # Special handling for JSON fields
        if field in ("main_points", "key_quotes", "tags") and value:
            value = json.dumps(value, ensure_ascii=False)
        
        update_parts.append(f"{db_col} = ?")
        update_values.append(value)
        
        update_parts.append(f"{fetched_at_col} = ?")
        update_values.append(now)
    
    # Always update bb_resource_id
    update_parts.append("bb_resource_id = ?")
    update_values.append(meta.get("bb_resource_id"))
    
    # Update bb_fetched_at if summary or one_sentence was fetched
    if "summary" in fields or "one_sentence" in fields:
        update_parts.append("bb_fetched_at = ?")
        update_values.append(now)
    
    update_values.append(article_id)
    
    sql = f"UPDATE articles SET {', '.join(update_parts)} WHERE id = ?"
    conn.execute(sql, update_values)
    conn.commit()
    
    logger.info(
        "[bestblogs_api] stored meta for article %s: fields=%s, resource=%s",
        article_id, fields, meta.get("bb_resource_id"),
    )
    return meta
