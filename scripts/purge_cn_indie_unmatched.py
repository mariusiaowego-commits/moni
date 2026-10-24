#!/usr/bin/env python3
"""One-off cleanup for chinese_independent_blogs rows ingested before v1.0.63.

Background
----------
v1.0.60 bound this source to a dedicated tag (``independent-blog``) whose
``keywords`` were deliberately empty.  That made ``apply_tag_keyword_filter``
a pass-through and, because ``ArticleRepository.dedupe_insert`` only writes
``article_tags`` for tags that were actually hit, every ingested row also
ended up with zero tags.  The result: 105 rows of unfiltered long-tail
content, invisible to tag filtering.

v1.0.63 removes that tag and rebinds the source to the project's existing
business tags (migration 037).  This script applies the new gate to the rows
that were already ingested:

* any article that hits at least one bound tag keyword is kept and gets the
  matched tags + ``article_keyword_hits`` rows written (the same shape a
  normal fetch would produce);
* any article that hits nothing is deleted outright, as requested — tag
  cascade removes its ``article_tags`` / ``article_keyword_hits`` rows.

The matching logic mirrors ``moni.fetch.tag_filter.apply_tag_keyword_filter``
so a cleaned database looks exactly like one populated after the fix.

Usage
-----
    /usr/local/bin/python3 scripts/purge_cn_indie_unmatched.py            # dry run
    /usr/local/bin/python3 scripts/purge_cn_indie_unmatched.py --apply    # execute
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

SOURCE_NAME = "chinese_independent_blogs"
DEFAULT_DB = Path(__file__).resolve().parent.parent / "data" / "news.db"


def load_tag_keywords(conn: sqlite3.Connection) -> dict[str, list[str]]:
    rows = conn.execute(
        """
        SELECT t.name, t.keywords
        FROM source_tags st
        JOIN sources s         ON s.id  = st.source_id
        JOIN tag_definitions t ON t.name = st.tag_name
        WHERE s.name = ? AND t.is_active = 1
        """,
        (SOURCE_NAME,),
    ).fetchall()
    result: dict[str, list[str]] = {}
    for name, raw in rows:
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
            result[name] = kws
    return result


def match_article(
    title: str, content: str, tag_keywords: dict[str, list[str]]
) -> dict[str, list[str]]:
    """Return {tag_name: [matched keywords]} for one article."""
    haystack = f"{title or ''}\n{content or ''}"
    hits: dict[str, list[str]] = {}
    for tag_name, kws in tag_keywords.items():
        matched = [kw for kw in kws if kw in haystack]
        if matched:
            hits[tag_name] = matched
    return hits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    parser.add_argument("--apply", action="store_true", help="write changes")
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"DB not found: {db_path}", file=sys.stderr)
        return 1

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")

    tag_keywords = load_tag_keywords(conn)
    if not tag_keywords:
        print(
            f"{SOURCE_NAME} has no keyword-carrying tags bound — run migration "
            "037 (and seed) first; refusing to treat the source as pass-through.",
            file=sys.stderr,
        )
        return 2

    articles = conn.execute(
        "SELECT id, title, raw_content FROM articles WHERE source = ?",
        (SOURCE_NAME,),
    ).fetchall()

    now = datetime.now(timezone.utc).isoformat()
    keep: list[tuple[str, dict[str, list[str]]]] = []
    drop: list[tuple[str, str]] = []
    for art in articles:
        hits = match_article(art["title"], art["raw_content"], tag_keywords)
        if hits:
            keep.append((art["id"], hits))
        else:
            drop.append((art["id"], art["title"] or "(无标题)"))

    print(f"source              : {SOURCE_NAME}")
    print(f"bound tags (w/ keywords): {len(tag_keywords)}")
    print(f"articles            : {len(articles)}")
    print(f"keep (>=1 tag hit)  : {len(keep)}")
    print(f"drop (no tag hit)   : {len(drop)}")
    print()
    print("kept samples:")
    for article_id, hits in keep[:10]:
        title = conn.execute(
            "SELECT title FROM articles WHERE id = ?", (article_id,)
        ).fetchone()["title"]
        print(f"  + {title[:46]:48s} {sorted(hits)}")
    print("dropped samples:")
    for _, title in drop[:10]:
        print(f"  - {title[:60]}")

    if not args.apply:
        print("\n[dry run] nothing written — rerun with --apply to execute.")
        conn.close()
        return 0

    with conn:
        for article_id, hits in keep:
            for tag_name, keywords in hits.items():
                conn.execute(
                    """
                    INSERT OR IGNORE INTO article_tags
                        (article_id, tag_name, predicted, created_at)
                    VALUES (?, ?, 0, ?)
                    """,
                    (article_id, tag_name, now),
                )
                for keyword in keywords:
                    exists = conn.execute(
                        """
                        SELECT 1 FROM article_keyword_hits
                        WHERE article_id = ? AND keyword = ? AND tag_name = ?
                        """,
                        (article_id, keyword, tag_name),
                    ).fetchone()
                    if exists:
                        continue
                    conn.execute(
                        """
                        INSERT INTO article_keyword_hits
                            (article_id, keyword, filter_id, hit_field,
                             tag_name, created_at)
                        VALUES (?, ?, NULL, 'title+content', ?, ?)
                        """,
                        (article_id, keyword, tag_name, now),
                    )
        for article_id, _ in drop:
            conn.execute("DELETE FROM articles WHERE id = ?", (article_id,))

    remaining = conn.execute(
        "SELECT COUNT(*) FROM articles WHERE source = ?", (SOURCE_NAME,)
    ).fetchone()[0]
    tagged = conn.execute(
        """
        SELECT COUNT(DISTINCT a.id)
        FROM articles a
        JOIN article_tags at ON at.article_id = a.id
        WHERE a.source = ?
        """,
        (SOURCE_NAME,),
    ).fetchone()[0]
    print(f"\napplied: kept {len(keep)}, deleted {len(drop)}")
    print(f"remaining for source: {remaining} (tagged {tagged})")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
