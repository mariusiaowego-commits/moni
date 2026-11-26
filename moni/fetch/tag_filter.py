"""Tag-driven content filter (v1.0.5.4).

Why
---
The user wanted to drive fetch-time content selection from the
**tag** system rather than from per-source keyword lists.  The model:

* every ``tag_definitions`` row has an optional ``keywords`` column
  (a JSON array of substrings — typically 2-10 phrases)
* every source has a set of bound tags (``source_tags``)
* when the fetcher evaluates a parsed article, it builds the UNION
  of keywords from every tag the article's source is bound to, then
  asks: "does the article's title+content contain ANY of those
  substrings?"
    * yes  -> keep
    * no   -> drop

The drop side is the "any-tag hit" semantics: even if a single tag
out of three is a hit, the article survives.  Tags that don't carry
keywords are skipped (no effect), so the existing 5 tag definitions
stay backwards-compatible — if no one has filled in ``keywords``,
the filter is a pass-through and the fetcher behaves as it did in
v1.0.5.3.

What's NOT here (intentionally)
-------------------------------
* No LLM scoring.  Substring match is deterministic, free, and
  Chinese-friendly.
* No per-tag thresholds.  Each tag contributes its keywords to the
  OR set; the user does not weight individual tags.
* No whitelist / blacklist split.  The whole mechanism is a
  "must match" gate.  If the user wants a blacklist-style "drop
  articles containing X", that already lives in
  ``source_keyword_filters`` (v1.0.2) and runs independently upstream
  of this filter.

How it's wired in
-----------------
``moni.fetch.base.BaseFetcher.run`` calls
``apply_tag_keyword_filter(article_list)`` AFTER the existing
``source_keyword_filters`` step.  Stats are surfaced in the same
``filter_stats`` dict under a new ``tag_filter`` key, so the fetch
log shows both pass/fail counts.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from typing import Any

from moni.db.connection import get_db
from .filters import _bypass_tag_or_gate
from .keyword_match import keyword_matches

logger = logging.getLogger(__name__)

#: 工程 / 跟踪类 tag 名单 —— 只用于追溯，**绝不**参与业务 tag 准入。
#:
#: t119u (2026-11-26, dad 拍板第 2 条): ``xbot-scope``（显示名「X-bot 范围」）
#: 是 migration 061 建的跟踪标签。历史实现会在「scope 救回」时把它当命中
#: 挂到文章上，导致它出现在用户的业务 tag 勾选列表里、看起来像业务 tag。
#: 现在统一在这里排除：tag 闸门不读它的关键词，fetcher 也不把它当业务命中。
ENGINEERING_TAG_NAMES: frozenset[str] = frozenset({"xbot-scope"})


def _load_bound_tag_names_for_source(source_name: str) -> list[str]:
    """Return the tag names bound to a source (for synthetic bypass hits)."""
    try:
        conn = get_db()
        rows = conn.execute(
            """
            SELECT t.name
            FROM source_tags st
            JOIN sources s        ON s.id = st.source_id
            JOIN tag_definitions t ON t.name = st.tag_name
            WHERE s.name = ? AND t.is_active = 1
            """,
            (source_name,),
        ).fetchall()
        return [r["name"] for r in rows]
    except Exception:  # noqa: BLE001
        return []


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


def _load_tag_keywords_for_source(source_name: str) -> dict[str, list[str]]:
    """Return a dict of {tag_name: [keywords]} from every tag bound
    to the given source (by source ``name``, e.g. ``mpaypass``).

    Tags whose ``keywords`` is NULL or empty are skipped — they
    contribute nothing to the OR set.  This is the "any tag with
    keywords is enough" semantics: even if just one of the bound
    tags has keywords, the filter still runs.

    v1.0.5.20: 返回 {tag_name: [keywords]} 而不是 [keywords]，
    以便记录每个关键词属于哪个 tag。

    t119u (2026-11-26): ``ENGINEERING_TAG_NAMES``（目前只有 ``xbot-scope``）
    在这里被**强制排除** —— 它是工程跟踪 tag，绝不能冒充业务 tag 参与准入。
    """
    conn = get_db()
    rows = conn.execute(
        """
        SELECT t.name, t.keywords
        FROM source_tags st
        JOIN sources s         ON s.id  = st.source_id
        JOIN tag_definitions t  ON t.name = st.tag_name
        WHERE s.name = ?
          AND t.is_active = 1
        """,
        (source_name,),
    ).fetchall()
    tag_keywords: dict[str, list[str]] = {}
    for r in rows:
        tag_name = r["name"]
        if tag_name in ENGINEERING_TAG_NAMES:
            # 工程 tag：既不进 OR 集合，也不产生命中（t119u AC-5）
            continue
        raw = r["keywords"]
        if not raw:
            continue
        try:
            arr = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            logger.warning(
                "[tag_filter] tag %r has invalid JSON keywords — skipping",
                tag_name,
            )
            continue
        if not isinstance(arr, list):
            continue
        kws = [kw.strip() for kw in arr if isinstance(kw, str) and kw.strip()]
        if kws:
            tag_keywords[tag_name] = kws
    return tag_keywords


def apply_tag_keyword_filter(
    source: str,
    articles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int], list[dict[str, Any]]]:
    """Drop articles whose title+content doesn't hit any of the
    source's tag-bound keywords.

    Returns ``(kept_articles, stats, keyword_hits)`` where ``stats`` has:
        * ``input``              — how many we received
        * ``kept``               — how many survived
        * ``dropped_tag_filter``  — how many were dropped here
        * ``tags_used``          — number of unique keywords in the
                                   OR set (0 means the filter was
                                   effectively disabled, no tags
                                   carry keywords)
        * ``keyword_hits``       — list of {article_url, keyword, tag_name, filter_id, hit_field}

    Empty keyword set → no-op (kept = input).  This is the
    backwards-compat path: a tag system with no keywords configured
    produces zero behaviour change from v1.0.5.3.
    """
    stats = {
        "input": len(articles),
        "kept": 0,
        "dropped_tag_filter": 0,
        "tags_used": 0,
        "dropped_articles": [],  # v1.0.6.4: 丢弃文章标题明细
    }
    if not articles:
        return articles, stats, []

    # ── Sprint-21 bypass: skip tag-keyword OR gate + synthesize bound tags ──
    # sprint-54 / id 26111703: switched from the combined ``_is_source_bypassed``
    # to the **narrow** ``_bypass_tag_or_gate`` so that the keyword-gate bypass
    # (``filters.py``) and the tag-gate bypass are decided independently.
    # Behaviour is unchanged for every source that has no explicit
    # ``bypass_tag_or_gate`` key (fallback keeps the old value).
    if _bypass_tag_or_gate(source):
        logger.info(
            "[%s] bypass_tag_or_gate — skipping tag filter and synthesizing hits",
            source,
        )
        bound_tags = _load_bound_tag_names_for_source(source)
        synthetic_hits: list[dict[str, Any]] = []
        for art in articles:
            art_url = art.get("url", "")
            for tag_name in bound_tags:
                synthetic_hits.append({
                    "article_url": art_url,
                    "keyword": "(bypass)",
                    "tag_name": tag_name,
                    "filter_id": None,
                    "hit_field": "bypass",
                })
        stats["kept"] = len(articles)
        stats["tags_used"] = len(bound_tags)
        return articles, stats, synthetic_hits

    tag_keywords = _load_tag_keywords_for_source(source)
    # 合并所有 tag 的关键词用于过滤（OR 语义）
    all_keywords: list[str] = []
    seen: set[str] = set()
    for kws in tag_keywords.values():
        for kw in kws:
            if kw not in seen:
                seen.add(kw)
                all_keywords.append(kw)

    stats["tags_used"] = len(all_keywords)
    if not all_keywords:
        # No tag is bound to this source, or no tag carries keywords.
        # Pass through unchanged.
        stats["kept"] = len(articles)
        return articles, stats, []

    # v1.0.5.20: 记录命中的关键词及其所属 tag
    keyword_hits: list[dict[str, Any]] = []
    kept: list[dict[str, Any]] = []
    for art in articles:
        title = (art.get("title") or "").strip()
        content = (art.get("content") or art.get("raw_content") or "").strip()
        # v1.0.5.24: bestblogs RSS descriptions are short snippets prone to
        # boilerplate false-positives — match title only.
        # v1.0.7.5: extend to all bestblog* subclasses (podcast/yt/x) —
        # their RSS/Atom <description> also contains boilerplate (subscribe
        # CTAs, sponsor mentions) that triggers false-positive tag matches.
        title_only = source.startswith("bestblog")
        art_url = art.get("url", "")

        # 检查每个 tag 的命中情况
        article_hit_tags: set[str] = set()
        for tag_name, kws in tag_keywords.items():
            for kw in kws:
                # v1.0.64: boundary-aware matching — see keyword_match.
                if keyword_matches(
                    kw,
                    title=title,
                    body=content,
                    field="title" if title_only else "title+content",
                ):
                    keyword_hits.append({
                        "article_url": art_url,
                        "keyword": kw,
                        "tag_name": tag_name,
                        "filter_id": None,
                        "hit_field": "title" if title_only else "title+content",
                    })
                    article_hit_tags.add(tag_name)

        if article_hit_tags:
            kept.append(art)
        else:
            stats["dropped_tag_filter"] += 1
            # v1.0.6.9: 丢弃时写 basket
            _insert_basket(art, "tag_filter", "no_tag_keyword_match")
            # v1.0.6.4: 记录丢弃文章标题 (最多 20 条)
            if len(stats["dropped_articles"]) < 20:
                stats["dropped_articles"].append({"title": title or "(无标题)"})

    stats["kept"] = len(kept)
    if stats["dropped_tag_filter"]:
        logger.info(
            "[tag_filter] %s: %d/%d kept (dropped %d by tag-keyword filter, %d keyword(s) in OR set)",
            source, stats["kept"], stats["input"],
            stats["dropped_tag_filter"], stats["tags_used"],
        )
    return kept, stats, keyword_hits
