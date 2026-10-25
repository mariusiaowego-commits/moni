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
from moni.fetch.keyword_match import (
    SHORT_CJK_MAX_LEN,
    keyword_matches,
    keyword_profile,
)


def _load_tags() -> dict[str, list[str]]:
    conn = get_db()
    out: dict[str, list[str]] = {}
    for row in conn.execute(
        "SELECT name, keywords FROM tag_definitions WHERE is_active = 1 ORDER BY name"
    ):
        raw = row["keywords"]
        if not raw:
            continue
        try:
            arr = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(arr, list):
            continue
        kws = [k.strip() for k in arr if isinstance(k, str) and k.strip()]
        if kws:
            out[row["name"]] = kws
    return out


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


def run_keyword_audit(args) -> int:
    source = getattr(args, "source", None)
    show_lost = getattr(args, "show_lost", 10)

    if source:
        tag_keywords = _load_bound_tags_for_source(source)
        if not tag_keywords:
            print(f"ERROR: 源 {source!r} 没有绑定任何带关键词的 tag")
            return 2
    else:
        tag_keywords = _load_tags()
        if not tag_keywords:
            print("ERROR: 没有任何 tag 配置了关键词")
            return 2

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

    all_kws = [kw for kws in tag_keywords.values() for kw in kws]
    kept_legacy = kept_new = total = 0
    lost: list[tuple[str, str]] = []
    kw_delta: dict[str, list[int]] = {}

    for row in _iter_articles(source):
        total += 1
        title = row["title"] or ""
        body = row["raw_content"] or ""
        row_title_only = scoped_title_only or (row["source"] or "").startswith("bestblog")
        legacy = _legacy_hits(all_kws, title, body, title_only=row_title_only)
        new = _v164_hits(all_kws, title, body, title_only=row_title_only)
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

    dropped = total - kept_new
    print(f"存量 {total} 篇")
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
