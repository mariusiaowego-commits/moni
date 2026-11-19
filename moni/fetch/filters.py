"""moni.fetch.filters — 按 global_keyword_filters + source_keyword_filters 过滤 article 列表.

被 BaseFetcher.run() 在 parse() 之后 / dedupe_insert() 之前调用.

## 行为与铁律优先级 (v1.0.40 sprint-20 PR2)

1. 全局黑名单 (global_blacklist): 任一全局黑名单命中 keyword → drop, reason='global_blacklist'
2. 源黑名单 (source_blacklist): 任一源黑名单命中 keyword → drop, reason='blacklist'
3. 全局白名单 (global_whitelist): 若存在 active 全局白名单，必须命中至少 1 个 keyword → 否则 drop, reason='global_whitelist'
4. 源白名单 (source_whitelist): 若存在 active 源白名单，必须命中至少 1 个 keyword → 否则 drop, reason='whitelist'
5. 下游交由 tag_filter (tag_keywords OR 门控)

黑名单 = 并集 (全局+源 任一命中丢)
白名单 = 交集 (全局白+源白 均需满足；无 active 白名单层则透传)
match_field 各自独立，互不污染。

## 返回

(filtered_articles, stats, keyword_hits)
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from moni.db.connection import get_db

from .keyword_match import matched_keywords

logger = logging.getLogger(__name__)


def _load_source_fetch_config(source_name: str) -> dict:
    """读 ``sources.fetch_config`` (解析失败 ⇒ 空 dict)。"""
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT fetch_config FROM sources WHERE name = ?",
            (source_name,),
        ).fetchone()
        if row and row["fetch_config"]:
            cfg = json.loads(row["fetch_config"])
            if isinstance(cfg, dict):
                return cfg
    except Exception:  # noqa: BLE001
        pass
    return {}


def _bypass_keyword_filters(source_name: str) -> bool:
    """Return True if the source skips the **global/source keyword** gates.

    Reads the canonical key ``bypass_keyword_filters``; when that key is
    **absent** it falls back to the **deprecated alias**
    ``bypass_tag_filter`` (sprint-54 / id 26111703).

    The alias stays an *alias of the canonical key* — it is **not** the new
    decoupled tag-gate switch.  Keeping the fallback means every config
    written before the split keeps its exact current behaviour: the
    canonical key wins, the alias only fills in when the canonical key is
    missing (the same precedence the old ``_is_source_bypassed`` had).

    Decoupling the tag gate is done by the **new** ``bypass_tag_or_gate``
    key, read by :func:`_bypass_tag_or_gate`.
    """
    cfg = _load_source_fetch_config(source_name)
    if "bypass_keyword_filters" in cfg:
        return bool(cfg["bypass_keyword_filters"])
    return bool(cfg.get("bypass_tag_filter", False))


def _bypass_tag_or_gate(source_name: str) -> bool:
    """Return True if the source skips the **tag-keyword OR gate**.

    This is the **decoupled** half of the old combined bypass
    (sprint-54 / id 26111703).

    Read order:

    1. ``bypass_tag_or_gate`` — the **new** key.  Present ⇒ it alone
       decides the tag gate, independently of the keyword gate.
    2. ``bypass_keyword_filters`` — the canonical old key.  Absent new key
       ⇒ fall back so every source configured before the split keeps its
       exact current behaviour (**zero behaviour change**).
    3. ``bypass_tag_filter`` — the deprecated alias, last resort, so a
       legacy alias-only config still behaves as it always did.

    Why a **new** key name instead of reusing ``bypass_tag_filter``:
    that name is already a *deprecated alias* of ``bypass_keyword_filters``
    in ``moni/web/routes/config_sources.py`` (the alias normalisation and
    its ``pop`` are covered by existing compatibility tests in
    ``tests/test_fetch_config.py``).  Reusing it would have made the split
    self-defeating — saving the config once would silently drop the new
    key again, and the two switches would keep meaning the same thing.
    """
    cfg = _load_source_fetch_config(source_name)
    if "bypass_tag_or_gate" in cfg:
        return bool(cfg["bypass_tag_or_gate"])
    if "bypass_keyword_filters" in cfg:
        return bool(cfg["bypass_keyword_filters"])
    return bool(cfg.get("bypass_tag_filter", False))


def _is_source_bypassed(source_name: str) -> bool:
    """Return True if the source short-circuits the gates (legacy helper).

    Kept for backward compatibility with callers that only need the
    combined answer.  Its semantics are **unchanged** from before the
    split: the canonical key ``bypass_keyword_filters`` wins, the
    deprecated alias ``bypass_tag_filter`` only fills in when the canonical
    key is missing.

    ⚠️ It does **not** know about the new ``bypass_tag_or_gate`` key — a
    source that decouples the tag gate must be asked with the two narrow
    helpers instead (:func:`_bypass_keyword_filters` /
    :func:`_bypass_tag_or_gate`).
    """
    return _bypass_keyword_filters(source_name)


def _insert_basket(art: dict, reason: str, dropped_by: str) -> None:
    """将被过滤丢弃的文章写入 basket 表."""
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()
    source = art.get("source", "")
    url = art.get("url", "")
    title = art.get("title", "")
    raw_id = f"{source}{url}{title}"
    art_id = hashlib.sha256(raw_id.encode()).hexdigest()[:16]
    try:
        conn.execute(
            """INSERT OR IGNORE INTO basket
               (id, title, url, source, publisher, published_at, fetched_at,
                raw_content, dropped_reason, dropped_by, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (art_id, title, url,
             source, art.get("publisher"), art.get("published_at"),
             art.get("fetched_at", now), art.get("content") or art.get("raw_content"),
             reason, dropped_by, now),
        )
        conn.commit()
    except Exception:
        logger.debug("[basket] failed to insert %s", url[:60])


def _resolve_source_id(source_name: str) -> Optional[str]:
    """按 source.name 找 sources.id. 没找到返回 None (表示不应用源级过滤)."""
    conn = get_db()
    row = conn.execute(
        "SELECT id FROM sources WHERE name = ?",
        (source_name,),
    ).fetchone()
    return row["id"] if row else None


def _load_active_global_filters() -> List[Dict[str, Any]]:
    """查 global_keyword_filters 所有 active filter."""
    conn = get_db()
    table_exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='global_keyword_filters'"
    ).fetchone()
    if not table_exists:
        return []
    rows = conn.execute(
        """
        SELECT id, mode, match_field, keywords
        FROM global_keyword_filters
        WHERE is_active = 1
        """
    ).fetchall()
    filters = []
    for r in rows:
        try:
            kw = json.loads(r["keywords"])
        except (TypeError, json.JSONDecodeError):
            logger.warning(
                "[filters] global_keyword_filters id=%d has invalid JSON keywords — skipping",
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
            "is_global": True,
        })
    return filters


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
            "is_global": False,
        })
    return filters


def _is_blacklist_match(article: Dict[str, Any], flt: Dict[str, Any]) -> Optional[str]:
    """黑名单: 返回命中的 keyword, 没命中返回 None."""
    hits = _filter_hits(article, flt)
    return hits[0] if hits else None


def _is_whitelist_pass(article: Dict[str, Any], flt: Dict[str, Any]) -> Optional[str]:
    """白名单: 返回命中的 keyword, 全不命中返回 None."""
    hits = _filter_hits(article, flt)
    return hits[0] if hits else None


def _filter_hits(article: Dict[str, Any], flt: Dict[str, Any]) -> List[str]:
    """该 filter 命中的所有 keyword (v1.0.64 匹配规则)."""
    return matched_keywords(
        flt["keywords"],
        title=article.get("title", "") or "",
        body=article.get("content", "") or article.get("raw_content", "") or "",
        field=flt["match_field"],
    )


def _append_hit(hits_list: List[Dict[str, Any]], hit: Dict[str, Any]) -> None:
    for existing in hits_list:
        if (
            existing.get("article_url") == hit.get("article_url")
            and existing.get("keyword") == hit.get("keyword")
            and existing.get("filter_id") == hit.get("filter_id")
            and existing.get("tag_name") == hit.get("tag_name")
        ):
            return
    hits_list.append(hit)


def apply_keyword_filters(
    source_name: str,
    articles: List[Dict[str, Any]],
) -> Tuple[List[Dict[str, Any]], Dict[str, int], List[Dict[str, Any]]]:
    """按 global_keyword_filters + source_keyword_filters 过滤 articles.

    Args:
        source_name: articles.source 的字符串 (e.g. '36kr_rss')
        articles:    parse() 后的 normalised article dicts

    Returns:
        (filtered_articles, stats, keyword_hits)
        stats 包含: input / kept / dropped_blacklist / dropped_whitelist /
                    dropped_global_blacklist / dropped_global_whitelist /
                    filters_applied / global_filters_applied
        keyword_hits 包含: 每篇文章命中的关键词列表
            [{article_url, keyword, filter_id, tag_name, hit_field}, ...]
    """
    stats = {
        "input": len(articles),
        "kept": 0,
        "dropped_blacklist": 0,
        "dropped_whitelist": 0,
        "dropped_global_blacklist": 0,
        "dropped_global_whitelist": 0,
        "filters_applied": 0,
        "global_filters_applied": 0,
    }

    # ── 0. Sprint-21 bypass: centrally-curated sources skip keyword gates ──
    # sprint-54 / id 26111703: switched from the combined ``_is_source_bypassed``
    # to the **narrow** ``_bypass_keyword_filters`` so this gate is decided
    # independently of the tag gate.  Behaviour is unchanged for every source
    # that has no explicit ``bypass_tag_filter`` key (fallback keeps the old
    # value); *enabling* the source-level blacklist for ``xbot_feed`` is a
    # configuration change that needs its own authorisation.
    if _bypass_keyword_filters(source_name):
        logger.info(
            "[%s] bypass_keyword_filters — skipping global/source black/whitelists",
            source_name,
        )
        stats["kept"] = len(articles)
        return articles, stats, []

    global_filters = _load_active_global_filters()
    source_id = _resolve_source_id(source_name)
    source_filters = _load_active_filters(source_id) if source_id is not None else []

    if not global_filters and not source_filters:
        # 无全局及源级 filter — 全保留
        stats["kept"] = len(articles)
        return articles, stats, []

    stats["global_filters_applied"] = len(global_filters)
    stats["filters_applied"] = len(global_filters) + len(source_filters)

    global_blacklists = [f for f in global_filters if f["mode"] == "blacklist"]
    global_whitelists = [f for f in global_filters if f["mode"] == "whitelist"]
    source_blacklists = [f for f in source_filters if f["mode"] == "blacklist"]
    source_whitelists = [f for f in source_filters if f["mode"] == "whitelist"]

    all_keyword_hits: List[Dict[str, Any]] = []
    kept: List[Dict[str, Any]] = []

    for art in articles:
        art_url = art.get("url", "")
        dropped = False

        # ── 1. 全局黑名单 (Global Blacklist) ──
        for flt in global_blacklists:
            hit = _is_blacklist_match(art, flt)
            if hit is not None:
                logger.debug(
                    "[%s] global blacklist drop: title=%r kw=%r",
                    source_name, art.get("title", "")[:50], hit,
                )
                stats["dropped_global_blacklist"] += 1
                stats["dropped_blacklist"] += 1
                _append_hit(all_keyword_hits, {
                    "article_url": art_url,
                    "keyword": hit,
                    "tag_name": "[global]",
                    "filter_id": None,
                    "hit_field": flt["match_field"],
                })
                for kw in _filter_hits(art, flt):
                    if kw != hit:
                        _append_hit(all_keyword_hits, {
                            "article_url": art_url,
                            "keyword": kw,
                            "tag_name": "[global]",
                            "filter_id": None,
                            "hit_field": flt["match_field"],
                        })
                _insert_basket(art, "global_blacklist", hit)
                dropped = True
                break
        if dropped:
            continue

        # ── 2. 源黑名单 (Source Blacklist) ──
        for flt in source_blacklists:
            hit = _is_blacklist_match(art, flt)
            if hit is not None:
                logger.debug(
                    "[%s] source blacklist drop: title=%r kw=%r",
                    source_name, art.get("title", "")[:50], hit,
                )
                stats["dropped_blacklist"] += 1
                _append_hit(all_keyword_hits, {
                    "article_url": art_url,
                    "keyword": hit,
                    "filter_id": flt["id"],
                    "hit_field": flt["match_field"],
                })
                for kw in _filter_hits(art, flt):
                    if kw != hit:
                        _append_hit(all_keyword_hits, {
                            "article_url": art_url,
                            "keyword": kw,
                            "filter_id": flt["id"],
                            "hit_field": flt["match_field"],
                        })
                _insert_basket(art, "blacklist", hit)
                dropped = True
                break
        if dropped:
            continue

        # ── 3. 全局白名单 (Global Whitelist) ──
        if global_whitelists:
            g_whitelist_passed = False
            for flt in global_whitelists:
                hit = _is_whitelist_pass(art, flt)
                if hit is not None:
                    g_whitelist_passed = True
                    _append_hit(all_keyword_hits, {
                        "article_url": art_url,
                        "keyword": hit,
                        "tag_name": "[global]",
                        "filter_id": None,
                        "hit_field": flt["match_field"],
                    })
                    for kw in _filter_hits(art, flt):
                        if kw != hit:
                            _append_hit(all_keyword_hits, {
                                "article_url": art_url,
                                "keyword": kw,
                                "tag_name": "[global]",
                                "filter_id": None,
                                "hit_field": flt["match_field"],
                            })
            if not g_whitelist_passed:
                logger.debug(
                    "[%s] global whitelist drop (no global kw matched): title=%r",
                    source_name, art.get("title", "")[:50],
                )
                stats["dropped_global_whitelist"] += 1
                stats["dropped_whitelist"] += 1
                _insert_basket(art, "global_whitelist", "no_global_whitelist_match")
                continue

        # ── 4. 源白名单 (Source Whitelist) ──
        if source_whitelists:
            s_whitelist_passed = False
            for flt in source_whitelists:
                hit = _is_whitelist_pass(art, flt)
                if hit is not None:
                    s_whitelist_passed = True
                    _append_hit(all_keyword_hits, {
                        "article_url": art_url,
                        "keyword": hit,
                        "filter_id": flt["id"],
                        "hit_field": flt["match_field"],
                    })
                    for kw in _filter_hits(art, flt):
                        if kw != hit:
                            _append_hit(all_keyword_hits, {
                                "article_url": art_url,
                                "keyword": kw,
                                "filter_id": flt["id"],
                                "hit_field": flt["match_field"],
                            })
            if not s_whitelist_passed:
                logger.debug(
                    "[%s] source whitelist drop (no kw matched): title=%r",
                    source_name, art.get("title", "")[:50],
                )
                stats["dropped_whitelist"] += 1
                _insert_basket(art, "whitelist", "no_keyword_match")
                continue

        # ── 5. 若未被拦截，记录存活文章命中所有规则的关键词 ──
        for flt in global_filters + source_filters:
            is_global = flt.get("is_global", False)
            for kw in _filter_hits(art, flt):
                hit_record = {
                    "article_url": art_url,
                    "keyword": kw,
                    "filter_id": None if is_global else flt["id"],
                    "hit_field": flt["match_field"],
                }
                if is_global:
                    hit_record["tag_name"] = "[global]"
                _append_hit(all_keyword_hits, hit_record)

        kept.append(art)

    stats["kept"] = len(kept)
    return kept, stats, all_keyword_hits
