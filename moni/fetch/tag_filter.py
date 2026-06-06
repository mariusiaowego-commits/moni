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

import json
import logging
from typing import Any

from moni.db.connection import get_db

logger = logging.getLogger(__name__)


def _load_tag_keywords_for_source(source_name: str) -> list[str]:
    """Return the deduplicated union of keywords from every tag bound
    to the given source (by source ``name``, e.g. ``mpaypass``).

    Tags whose ``keywords`` is NULL or empty are skipped — they
    contribute nothing to the OR set.  This is the "any tag with
    keywords is enough" semantics: even if just one of the bound
    tags has keywords, the filter still runs.
    """
    conn = get_db()
    rows = conn.execute(
        """
        SELECT t.keywords
        FROM source_tags st
        JOIN sources s         ON s.id  = st.source_id
        JOIN tag_definitions t  ON t.name = st.tag_name
        WHERE s.name = ?
          AND t.is_active = 1
        """,
        (source_name,),
    ).fetchall()
    keywords: list[str] = []
    for r in rows:
        raw = r["keywords"]
        if not raw:
            continue
        try:
            arr = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            logger.warning(
                "[tag_filter] tag %r has invalid JSON keywords — skipping",
                # tag name isn't in the SELECT — we have only its keywords.
                # Logging the raw value is enough to find it.
                raw,
            )
            continue
        if not isinstance(arr, list):
            continue
        for kw in arr:
            if isinstance(kw, str) and kw.strip():
                keywords.append(kw.strip())
    # De-dupe while preserving order (so test assertions stay stable).
    seen: set[str] = set()
    out: list[str] = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            out.append(kw)
    return out


def apply_tag_keyword_filter(
    source: str,
    articles: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, int]]:
    """Drop articles whose title+content doesn't hit any of the
    source's tag-bound keywords.

    Returns ``(kept_articles, stats)`` where ``stats`` has:
        * ``input``              — how many we received
        * ``kept``               — how many survived
        * ``dropped_tag_filter``  — how many were dropped here
        * ``tags_used``          — number of unique keywords in the
                                   OR set (0 means the filter was
                                   effectively disabled, no tags
                                   carry keywords)

    Empty keyword set → no-op (kept = input).  This is the
    backwards-compat path: a tag system with no keywords configured
    produces zero behaviour change from v1.0.5.3.
    """
    stats = {
        "input": len(articles),
        "kept": 0,
        "dropped_tag_filter": 0,
        "tags_used": 0,
    }
    if not articles:
        return articles, stats

    keywords = _load_tag_keywords_for_source(source)
    stats["tags_used"] = len(keywords)
    if not keywords:
        # No tag is bound to this source, or no tag carries keywords.
        # Pass through unchanged.
        stats["kept"] = len(articles)
        return articles, stats

    kept: list[dict[str, Any]] = []
    for art in articles:
        title = (art.get("title") or "").strip()
        content = (art.get("content") or art.get("raw_content") or "").strip()
        haystack = title + "\n" + content
        hit = any(kw in haystack for kw in keywords)
        if hit:
            kept.append(art)
        else:
            stats["dropped_tag_filter"] += 1

    stats["kept"] = len(kept)
    if stats["dropped_tag_filter"]:
        logger.info(
            "[tag_filter] %s: %d/%d kept (dropped %d by tag-keyword filter, %d keyword(s) in OR set)",
            source, stats["kept"], stats["input"],
            stats["dropped_tag_filter"], stats["tags_used"],
        )
    return kept, stats
