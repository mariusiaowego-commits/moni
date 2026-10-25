"""``moni keyword-audit`` — replay the current corpus through both the
old substring rule and the v1.0.64 match rules (read-only).

Why this exists
---------------
Changing a match rule silently changes how every source filters.  The
numbers you need *before* trusting it are: how many stored articles
would still pass, which keywords account for the difference, and which
articles would disappear.  Guessing at that from unit tests is not
enough — the tests only cover the shapes someone thought to write.

Everything here is read-only: no writes, no basket inserts, no fetch.
"""

from __future__ import annotations

import json
from typing import Any

from moni.db.connection import get_db
from moni.fetch.filters import _is_source_bypassed
from moni.fetch.keyword_match import (
    SHORT_CJK_MAX_LEN,
    keyword_matches,
    keyword_profile,
)


def _load_bound_tags_for_source(source: str) -> dict[str, list[str]]:
    """Only the tags actually bound to this source — mirrors the gate."""
    conn = get_db()
    out: dict[str, list[str]] = {}
    for row in conn.execute(
        """
        SELECT t.name, t.keywords
        FROM source_tags st
        JOIN sources s          ON s.id = st.source_id
        JOIN tag_definitions t  ON t.name = st.tag_name
        WHERE s.name = ? AND t.is_active = 1
        """,
        (source,),
    ):
        raw = row["keywords"]
        if not raw:
            continue
        try:
            arr = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        kws = [k.strip() for k in arr if isinstance(k, str) and k.strip()]
        if kws:
            out[row["name"]] = kws
    return out


def _load_all_bound_tags() -> dict[str, dict[str, list[str]]]:
    """``{source_name: {tag_name: [keywords]}}`` for every source.

    The real gate unions only the tags bound to *that* source, so an
    all-source replay that unions every tag everywhere over-reports
    survivors.  Measured gap in the 2026-10-25 snapshot: 190 articles.
    """
    conn = get_db()
    out: dict[str, dict[str, list[str]]] = {}
    for row in conn.execute(
        """
        SELECT s.name AS source_name, t.name AS tag_name, t.keywords
        FROM source_tags st
        JOIN sources s          ON s.id = st.source_id
        JOIN tag_definitions t  ON t.name = st.tag_name
        WHERE t.is_active = 1
        """
    ):
        raw = row["keywords"]
        if not raw:
            continue
        try:
            arr = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        kws = [k.strip() for k in arr if isinstance(k, str) and k.strip()]
        if kws:
            out.setdefault(row["source_name"], {})[row["tag_name"]] = kws
    return out


def _legacy_hits(
    kws: list[str], title: str, body: str, title_only: bool = False
) -> list[str]:
    """The pre-v1.0.64 rule: bare substring, full text.

    ``title_only`` must mirror the *old* behaviour too, not just the new
    one — ``bestblog*`` sources have always been title-only since
    v1.0.5.24/7.5.  Scoring their baseline against title+content would
    invent a "lost" article the old gate would never have kept.
    """
    haystack = title if title_only else title + "\n" + body
    return [kw for kw in kws if kw and kw in haystack]


def _v164_hits(kws: list[str], title: str, body: str, title_only: bool = False) -> list[str]:
    field = "title" if title_only else "title+content"
    return [
        kw
        for kw in kws
        if kw and keyword_matches(kw, title=title, body=body, field=field)
    ]


def _iter_articles(source: str | None):
    conn = get_db()
    sql = "SELECT source, title, raw_content FROM articles"
    params: tuple[Any, ...] = ()
    if source:
        sql += " WHERE source = ?"
        params = (source,)
    sql += " ORDER BY source, title"
    yield from conn.execute(sql, params)


def _bypassed_sources() -> set[str]:
    """Sources whose ``bypass_keyword_filters`` short-circuits every gate.

    They must be excluded from the delta, otherwise the report blames
    the matcher for articles no gate ever looked at (``follow_builders``
    is the live example).

    Deliberately not filtered on ``is_active``: the stored articles were
    inserted while the source was on, so they still bypassed the gate.
    ``follow_builders`` is inactive today and its 38 stored rows would
    otherwise be scored as if a matcher had seen them.
    """
    conn = get_db()
    out: set[str] = set()
    for row in conn.execute("SELECT name FROM sources"):
        if _is_source_bypassed(row["name"]):
            out.add(row["name"])
    return out


def run_keyword_audit(args) -> int:
    source = getattr(args, "source", None)
    show_lost = getattr(args, "show_lost", 10)

    if source:
        tag_keywords = _load_bound_tags_for_source(source)
        if not tag_keywords:
            print(f"ERROR: 源 {source!r} 没有绑定任何带关键词的 tag")
            return 2
        per_source_tags: dict[str, dict[str, list[str]]] = {source: tag_keywords}
    else:
        per_source_tags = _load_all_bound_tags()
        if not per_source_tags:
            print("ERROR: 没有任何 tag 配置了关键词")
            return 2
        # Union purely for the headline "rules in play" summary.
        tag_keywords = {}
        for src_tags in per_source_tags.values():
            for tag_name, kws in src_tags.items():
                merged = tag_keywords.setdefault(tag_name, [])
                for kw in kws:
                    if kw not in merged:
                        merged.append(kw)

    # bestblog* sources are title-only (v1.0.5.24/7.5).  In all-source mode
    # this has to be decided per row, not globally — otherwise their
    # baseline is scored against title+content and the report invents
    # "lost" articles the old gate never kept.
    scoped_title_only = bool(source) and source.startswith("bestblog")

    print(f"范围: {source or '全部信源'} | tag: {len(tag_keywords)} | "
          f"关键词: {sum(len(v) for v in tag_keywords.values())}")
    profile = keyword_profile([kw for kws in tag_keywords.values() for kw in kws])
    print(
        f"规则分布: ASCII 词边界 {profile['ascii_boundary']} | "
        f"≤{SHORT_CJK_MAX_LEN} 字中文 {profile['short_han']} | "
        f"普通子串 {profile['substring']} | 空 {profile['empty']}"
    )
    if scoped_title_only:
        print("注意: bestblog* 系只匹配标题 (v1.0.5.24/7.5 既有行为)")
    print()

    kept_legacy = kept_new = total = 0
    bypassed = _bypassed_sources()
    skipped = 0
    ungated = 0
    lost: list[tuple[str, str]] = []
    kw_delta: dict[str, list[int]] = {}

    for row in _iter_articles(source):
        total += 1
        row_source = row["source"] or ""
        if row_source in bypassed:
            # No gate runs on these — counting them would attribute a
            # delta to a matcher that never saw them.
            skipped += 1
            continue
        # The real gate unions only the tags bound to *this* source.
        row_tag_keywords = per_source_tags.get(row_source)
        if not row_tag_keywords:
            # Source has no bound tags with keywords → the gate is a
            # pass-through, exactly like `apply_tag_keyword_filter`.
            ungated += 1
            kept_legacy += 1
            kept_new += 1
            continue
        row_kws = [kw for kws in row_tag_keywords.values() for kw in kws]
        title = row["title"] or ""
        body = row["raw_content"] or ""
        row_title_only = row_source.startswith("bestblog")
        legacy = _legacy_hits(row_kws, title, body, title_only=row_title_only)
        new = _v164_hits(row_kws, title, body, title_only=row_title_only)
        if legacy:
            kept_legacy += 1
        if new:
            kept_new += 1
        if legacy and not new:
            lost.append((row["source"], title))
        for kw in set(legacy) | set(new):
            slot = kw_delta.setdefault(kw, [0, 0])
            if kw in legacy:
                slot[0] += 1
            if kw in new:
                slot[1] += 1

    gated = total - skipped
    dropped = gated - kept_new
    print(f"存量 {total} 篇" + (f" (跳过 {skipped} 篇 bypass 源)" if skipped else ""))
    if skipped:
        print(f"  受闸门约束:        {gated} 篇")
    if ungated:
        print(f"  其中无绑定 tag (pass-through): {ungated} 篇")
    print(f"  旧规则(纯子串) 存活: {kept_legacy}")
    print(f"  v1.0.64 规则存活:   {kept_new}")
    print(f"  新规则丢弃:        {dropped}  (其中 {len(lost)} 篇是旧规则本会留下)")

    changed = {k: v for k, v in kw_delta.items() if v[0] != v[1]}
    if changed:
        print("\n受影响的单个关键词 (旧命中 -> 新命中):")
        for kw, (old, new) in sorted(changed.items(), key=lambda kv: -(kv[1][0] - kv[1][1])):
            print(f"  {kw!r:20} {old:5} -> {new:5}")
    else:
        print("\n没有任何单个关键词的命中数发生变化。")

    if lost and show_lost:
        print(f"\n会因此不再命中的文章 (前 {min(show_lost, len(lost))} 篇):")
        for src, title in lost[:show_lost]:
            print(f"  [{src}] {title[:70]}")

    return 0
