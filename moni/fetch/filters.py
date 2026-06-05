"""moni.fetch.filters — 按 source_keyword_filters 过滤 article 列表.

被 BaseFetcher.run() 在 parse() 之后 / dedupe_insert() 之前调用.

## 行为

- 查 sources 表, 按 name 找 id
- 查 source_keyword_filters 该 source 的所有 active filter
- 无 filter → 全保留
- 黑名单模式: title 或 content 命中任一 keyword → drop
- 白名单模式: title 或 content 必须命中至少 1 个 keyword → 否则 drop
- 多 filter 按 (source_id, mode) 唯一: 每个 mode 只有 1 个 active

## 返回

(dict, list) — 统计 + 过滤后的 articles.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Dict, List, Optional, Tuple

from moni.db.connection import get_db

logger = logging.getLogger(__name__)


logger = logging.getLogger(__name__)


def _resolve_source_id(source_name: str) -> Optional[str]:
    """按 source.name 找 sources.id. 没找到返回 None (表示不应用过滤)."""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM sources WHERE name = ?",
        (source_name,),
    ).fetchone()
    return row["id"] if row else None


def _load_active_filters(source_id: str) -> List[Dict[str, Any]]:
    """查 source_keyword_filters 该 source_id 的所有 active filter."""
    conn = get_db()
    rows = conn.execute(
        """
        SELECT id, mode, match_field, keywords
        FROM source_keyword_filters
        WHERE source_id = ? AND is_active = 1
        """,
        (source_id,),
    ).fetchall()
    filters = []
    for r in rows:
        try:
            kw = json.loads(r["keywords"])
        except (TypeError, json.JSONDecodeError):
            logger.warning(
                "[filters] source_keyword_filters id=%d has invalid JSON keywords — skipping",
                r["id"],
            )
            continue
        if not isinstance(kw, list):
            continue
        filters.append({
            "id": r["id"],
            "mode": r["mode"],
            "match_field": r["match_field"],
            "keywords": [str(k) for k in kw],
        })
    return filters


def _match_field(article: Dict[str, Any], match_field: str) -> str:
    """取 article 用于匹配的文本."""
    if match_field == "title":
        return article.get("title", "") or ""
    if match_field == "content":
        return article.get("content", "") or article.get("raw_content", "") or ""
    # default: title+content
    title = article.get("title", "") or ""
    content = article.get("content", "") or article.get("raw_content", "") or ""
    return title + "\n" + content


def _is_blacklist_match(article: Dict[str, Any], flt: Dict[str, Any]) -> Optional[str]:
    """黑名单: 返回命中的 keyword, 没命中返回 None."""
    text = _match_field(article, flt["match_field"])
    for kw in flt["keywords"]:
        if kw and kw in text:
            return kw
    return None


def _is_whitelist_pass(article: Dict[str, Any], flt: Dict[str, Any]) -> Optional[str]:
    """白名单: 返回命中的 keyword, 全不命中返回 None."""
    text = _match_field(article, flt["match_field"])
    for kw in flt["keywords"]:
        if kw and kw in text:
            return kw
    return None


def apply_keyword_filters(
    source_name: str,
    articles: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int]]:
    """按 sources 关联的 source_keyword_filters 过滤 articles.

    Args:
        source_name: articles.source 的字符串 (e.g. '36kr_rss')
        articles:    parse() 后的 normalised article dicts

    Returns:
        (filtered_articles, stats)
        stats 包含: input / kept / dropped_blacklist / dropped_whitelist
    """
    stats = {
        "input": len(articles),
        "kept": 0,
        "dropped_blacklist": 0,
        "dropped_whitelist": 0,
        "filters_applied": 0,
    }

    source_id = _resolve_source_id(source_name)
    if source_id is None:
        # source 不在 sources 表里 — 不应用过滤, 全保留
        stats["kept"] = len(articles)
        return articles, stats

    filters = _load_active_filters(source_id)
    if not filters:
        stats["kept"] = len(articles)
        return articles, stats

    stats["filters_applied"] = len(filters)

    kept: List[Dict[str, Any]] = []
    for art in articles:
        # 先黑名单 (drop 命中)
        blacklisted = False
        for flt in filters:
            if flt["mode"] != "blacklist":
                continue
            hit = _is_blacklist_match(art, flt)
            if hit is not None:
                logger.debug(
                    "[%s] blacklist drop: title=%r kw=%r",
                    source_name, art.get("title", "")[:50], hit,
                )
                stats["dropped_blacklist"] += 1
                blacklisted = True
                break
        if blacklisted:
            continue

        # 后白名单 (没命中就 drop)
        whitelists = [f for f in filters if f["mode"] == "whitelist"]
        if whitelists:
            passed = False
            for flt in whitelists:
                hit = _is_whitelist_pass(art, flt)
                if hit is not None:
                    passed = True
                    break
            if not passed:
                logger.debug(
                    "[%s] whitelist drop (no kw matched): title=%r",
                    source_name, art.get("title", "")[:50],
                )
                stats["dropped_whitelist"] += 1
                continue

        kept.append(art)

    stats["kept"] = len(kept)
    return kept, stats
