#!/usr/bin/env python3
"""Backfill article_keyword_hits for historical articles.

v1.0.5.20: 对 keyword_hits 功能实现之前入库的历史数据，
重新计算并记录 keyword_hits。

Usage:
    python3 scripts/backfill_keyword_hits.py [--dry-run]
"""

import argparse
import json
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent.parent / "data" / "news.db"


def get_tag_keywords(conn: sqlite3.Connection, source_name: str) -> list[str]:
    """获取 source 绑定的所有 tag 的关键词（去重）"""
    rows = conn.execute("""
        SELECT t.keywords
        FROM source_tags st
        JOIN sources s ON s.id = st.source_id
        JOIN tag_definitions t ON t.name = st.tag_name
        WHERE s.name = ? AND t.is_active = 1
    """, (source_name,)).fetchall()
    
    keywords = []
    seen = set()
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
        for kw in arr:
            if isinstance(kw, str) and kw.strip() and kw not in seen:
                seen.add(kw)
                keywords.append(kw.strip())
    return keywords


def backfill(conn: sqlite3.Connection, dry_run: bool = False) -> dict:
    """对历史数据重新计算 keyword_hits"""
    stats = {
        "scanned": 0,
        "updated": 0,
        "skipped": 0,
        "errors": 0,
    }
    
    # 获取所有没有 keyword_hits 的文章
    rows = conn.execute("""
        SELECT a.id, a.title, a.url, a.source, a.raw_content
        FROM articles a
        WHERE a.id NOT IN (SELECT DISTINCT article_id FROM article_keyword_hits)
        ORDER BY a.fetched_at DESC
    """).fetchall()
    
    stats["scanned"] = len(rows)
    print(f"Found {len(rows)} articles without keyword_hits")
    
    for row in rows:
        article_id = row["id"]
        title = row["title"] or ""
        content = row["raw_content"] or ""
        source = row["source"]
        haystack = title + "\n" + content
        
        keywords = get_tag_keywords(conn, source)
        if not keywords:
            stats["skipped"] += 1
            continue
        
        # 找出命中的关键词
        hit_kws = list(dict.fromkeys(kw for kw in keywords if kw in haystack))
        
        if not hit_kws:
            stats["skipped"] += 1
            continue
        
        if dry_run:
            print(f"  [DRY-RUN] {title[:50]}... -> {hit_kws}")
            stats["updated"] += 1
            continue
        
        # 写入 keyword_hits
        try:
            for kw in hit_kws:
                conn.execute("""
                    INSERT INTO article_keyword_hits (article_id, keyword, hit_field, created_at)
                    VALUES (?, ?, 'title+content', datetime('now'))
                """, (article_id, kw))
            stats["updated"] += 1
        except Exception as e:
            print(f"  [ERROR] {article_id}: {e}")
            stats["errors"] += 1
    
    if not dry_run:
        conn.commit()
    
    return stats


def main():
    parser = argparse.ArgumentParser(description="Backfill keyword_hits for historical articles")
    parser.add_argument("--dry-run", action="store_true", help="Don't write to DB, just show what would be done")
    args = parser.parse_args()
    
    if not DB_PATH.exists():
        print(f"Error: DB not found at {DB_PATH}")
        return 1
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    
    print("Backfilling keyword_hits for historical articles...")
    stats = backfill(conn, dry_run=args.dry_run)
    
    print(f"\nDone:")
    print(f"  Scanned: {stats['scanned']}")
    print(f"  Updated: {stats['updated']}")
    print(f"  Skipped: {stats['skipped']}")
    print(f"  Errors:  {stats['errors']}")
    
    conn.close()
    return 0


if __name__ == "__main__":
    exit(main())
