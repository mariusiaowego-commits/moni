#!/usr/bin/env python3
"""Backfill keyword_hits for articles that predate v1.0.5.20 recording.

v1.0.5.22 — run once, idempotent.

What it does:
1. Find articles without any keyword_hits
2. For each, run tag keyword matching (same logic as tag_filter.py)
3. Also check source_keyword_filters (blacklist mode — record what would have been dropped)
4. Insert hits into article_keyword_hits
"""

import json
import sqlite3
import sys
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "news.db"


def main():
    if not DB_PATH.exists():
        print(f"DB not found: {DB_PATH}", file=sys.stderr)
        sys.exit(1)

    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Find articles without keyword_hits
    rows = conn.execute("""
        SELECT a.id, a.title, a.url, a.source, a.raw_content
        FROM articles a
        WHERE a.id NOT IN (SELECT DISTINCT article_id FROM article_keyword_hits)
    """).fetchall()

    if not rows:
        print("All articles already have keyword_hits. Nothing to backfill.")
        conn.close()
        return

    print(f"Found {len(rows)} articles without keyword_hits. Backfilling...")

    # Pre-load tag keywords per source
    tag_keywords_cache: dict[str, dict[str, list[str]]] = {}
    sources = set(r["source"] for r in rows)
    for src in sources:
        tag_kws = _load_tag_keywords(conn, src)
        tag_keywords_cache[src] = tag_kws

    # Pre-load source_keyword_filters per source
    source_filters_cache: dict[str, list[dict]] = {}
    for src in sources:
        source_filters_cache[src] = _load_source_filters(conn, src)

    inserted = 0
    skipped = 0
    for row in rows:
        article_id = row["id"]
        title = (row["title"] or "").strip()
        content = (row["raw_content"] or "").strip()
        haystack = title + "\n" + content
        source = row["source"]

        hits: list[dict] = []

        # 1. Check source_keyword_filters (blacklist mode)
        for flt in source_filters_cache.get(source, []):
            if flt["mode"] == "blacklist":
                text = haystack if flt["match_field"] == "title+content" else title
                for kw in flt["keywords"]:
                    if kw and kw in text:
                        hits.append({
                            "article_id": article_id,
                            "keyword": kw,
                            "filter_id": flt["id"],
                            "hit_field": flt["match_field"],
                            "tag_name": None,
                        })

        # 2. Check tag keywords
        for tag_name, kws in tag_keywords_cache.get(source, {}).items():
            for kw in kws:
                if kw and kw in haystack:
                    hits.append({
                        "article_id": article_id,
                        "keyword": kw,
                        "filter_id": None,
                        "hit_field": "title+content",
                        "tag_name": tag_name,
                    })

        if hits:
            # Deduplicate by keyword (same keyword may appear in multiple tags)
            seen = set()
            for h in hits:
                key = (h["article_id"], h["keyword"])
                if key in seen:
                    continue
                seen.add(key)
                conn.execute(
                    "INSERT INTO article_keyword_hits "
                    "(article_id, keyword, filter_id, hit_field, tag_name, created_at) "
                    "VALUES (?, ?, ?, ?, ?, datetime('now'))",
                    (h["article_id"], h["keyword"], h["filter_id"], h["hit_field"], h["tag_name"]),
                )
            inserted += 1
        else:
            skipped += 1

    conn.commit()
    conn.close()
    print(f"Done: {inserted} articles backfilled, {skipped} had no matches.")


def _load_tag_keywords(conn: sqlite3.Connection, source_name: str) -> dict[str, list[str]]:
    rows = conn.execute("""
        SELECT t.name, t.keywords
        FROM source_tags st
        JOIN sources s ON s.id = st.source_id
        JOIN tag_definitions t ON t.name = st.tag_name
        WHERE s.name = ? AND t.is_active = 1
    """, (source_name,)).fetchall()

    result: dict[str, list[str]] = {}
    for r in rows:
        raw = r["keywords"]
        if not raw:
            continue
        try:
            arr = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if not isinstance(arr, list):
            continue
        kws = [kw.strip() for kw in arr if isinstance(kw, str) and kw.strip()]
        if kws:
            result[r["name"]] = kws
    return result


def _load_source_filters(conn: sqlite3.Connection, source_name: str) -> list[dict]:
    rows = conn.execute("""
        SELECT skf.id, skf.mode, skf.keywords, skf.match_field
        FROM source_keyword_filters skf
        JOIN sources s ON s.id = skf.source_id
        WHERE s.name = ?
    """, (source_name,)).fetchall()

    result: list[dict] = []
    for r in rows:
        try:
            kws = json.loads(r["keywords"])
        except (TypeError, json.JSONDecodeError):
            kws = []
        result.append({
            "id": r["id"],
            "mode": r["mode"],
            "keywords": kws,
            "match_field": r["match_field"],
        })
    return result


if __name__ == "__main__":
    main()
