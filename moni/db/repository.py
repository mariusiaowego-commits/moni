"""ArticleRepository — CRUD operations on the articles table."""

from __future__ import annotations

import hashlib
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence

from .connection import get_db

logger = logging.getLogger(__name__)


class ArticleRepository:
    """Repository for article persistence.

    v1.0: single-method ``dedupe_insert()`` is all the fetch pipeline needs.
    """

    def dedupe_insert(self, articles: Sequence[dict[str, Any]], keyword_hits: Sequence[dict[str, Any]] = ()) -> int:
        """Insert articles, skipping rows whose (source, url) already exist.

        v1.0.5: also copies ``source_tags`` into ``article_tags`` for every
        newly-inserted row, so the article list view can show a classification
        badge immediately (and the source filter can be driven from a single
        JOIN).  Pre-existing rows are not retroactively tagged — use
        ``backfill_article_tags_from_source_tags`` for that.

        v1.0.5.20: records keyword hits into ``article_keyword_hits`` table
        so the frontend can highlight which keywords matched.

        Args:
            articles: Sequence of normalised article dicts.
                Required keys: ``title``, ``url``, ``source``.
                Optional keys: ``published_at``, ``content``, ``publisher``.
            keyword_hits: Sequence of keyword hit dicts.
                Each dict: {article_url, keyword, filter_id, hit_field}

        Returns:
            Number of rows actually inserted (i.e. new articles, not duplicates).
        """
        if not articles:
            return 0

        conn = get_db()
        inserted = 0
        # v1.0.10: newly inserted ids → local body layout for offline HTML
        new_ids: list[str] = []

        # v1.0.5.20: 建立 url -> article_id 的映射，用于记录 keyword_hits
        url_to_id: dict[str, str] = {}

        for art in articles:
            title: str = art["title"]
            url: str = art["url"]
            source: str = art["source"]

            # id = sha256(source + url + title)[:16]
            raw_id = f"{source}{url}{title}"
            aid = hashlib.sha256(raw_id.encode()).hexdigest()[:16]

            now = datetime.now(timezone.utc).isoformat()

            try:
                conn.execute(
                    """
                    INSERT OR IGNORE INTO articles
                        (id, title, url, source, publisher, published_at,
                         fetched_at, raw_content, bb_resource_id)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        aid,
                        title,
                        url,
                        source,
                        art.get("publisher"),
                        art.get("published_at"),
                        now,
                        art.get("content") or art.get("raw_content"),
                        art.get("bb_resource_id"),
                    ),
                )
                # rowcount is 0 for IGNORE'd rows, 1 for inserted rows
                n = conn.execute("SELECT changes()").fetchone()[0]
                inserted += n
                if n:
                    # v1.0.5.20: 不再复制 source 的所有 tag
                    # 改为在 keyword_hits 写入后只插入命中的 tag
                    new_ids.append(aid)
                # v1.0.5.20: 记录 url -> article_id 映射（不管是否新插入）
                url_to_id[url] = aid
            except Exception as exc:
                logger.warning("Failed to insert article %s (%s): %s", url, source, exc)

        conn.commit()

        # v1.0.5.20: 记录 keyword hits + 只插入命中的 tag
        if keyword_hits:
            self._insert_keyword_hits(conn, keyword_hits, url_to_id)
            self._insert_hit_tags(conn, keyword_hits, url_to_id)

        # v1.0.10: every new article gets offline-readable body layout (local, no LLM)
        if new_ids:
            try:
                from moni.share.enrichment import apply_local_body_layout_for_ids

                layout_stats = apply_local_body_layout_for_ids(new_ids)
                logger.info(
                    "body layout (local) after insert: updated=%s skipped=%s failed=%s",
                    layout_stats.get("updated"),
                    layout_stats.get("skipped"),
                    layout_stats.get("failed"),
                )
            except Exception as exc:
                logger.warning("body layout after insert failed: %s", exc)

        return inserted

    @staticmethod
    def _copy_source_tags_to_article(
        article_id: str, source_name: str, *, predicted: int = 0
    ) -> int:
        """Copy every active tag the source has onto the given article.

        Uses the source ``name`` (e.g. ``mpaypass``) to look up the source
        row, then walks ``source_tags`` for the source id.  Returns the
        number of ``article_tags`` rows actually inserted (i.e. excludes
        rows that were already there from a prior fetch).

        Why ``name`` not ``id``: the fetcher pipeline keys articles by
        ``source_name`` (``moni.fetch.run`` / ``moni.fetch.base``) while
        the API config UI keys sources by ``id`` (``src-mpaypass``).
        Joining on ``name`` is the only point of overlap.
        """
        conn = get_db()
        src_row = conn.execute(
            "SELECT id FROM sources WHERE name = ?", (source_name,)
        ).fetchone()
        if not src_row:
            return 0
        source_id = src_row["id"]
        tag_rows = conn.execute(
            "SELECT tag_name FROM source_tags WHERE source_id = ?",
            (source_id,),
        ).fetchall()
        if not tag_rows:
            return 0
        now = datetime.now(timezone.utc).isoformat()
        copied = 0
        for t in tag_rows:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO article_tags
                    (article_id, tag_name, predicted, created_at)
                VALUES (?, ?, ?, ?)
                """,
                (article_id, t["tag_name"], predicted, now),
            )
            copied += cur.rowcount
        return copied

    @staticmethod
    def backfill_article_tags_from_source_tags() -> dict[str, int]:
        """One-shot backfill: copy ``source_tags`` -> ``article_tags`` for
        every article in the DB whose tag list is currently empty.

        Useful when:
          * upgrading from a pre-v1.0.5 install where ``article_tags``
            was never populated at insert time, or
          * a user adds a new tag to a source and wants existing
            articles from that source to inherit the new tag.

        Returns a stats dict: ``scanned``, ``tagged``, ``already_tagged``.
        Idempotent: re-running on an already-tagged DB is a no-op.
        """
        conn = get_db()
        rows = conn.execute(
            """
            SELECT a.id AS aid, a.source AS sname
            FROM articles a
            LEFT JOIN article_tags t ON t.article_id = a.id
            WHERE t.tag_name IS NULL
            """
        ).fetchall()
        scanned = len(rows)
        tagged = 0
        already_tagged = 0
        for r in rows:
            n = ArticleRepository._copy_source_tags_to_article(
                r["aid"], r["sname"], predicted=0
            )
            if n:
                tagged += 1
            else:
                already_tagged += 1
        conn.commit()
        return {
            "scanned": scanned,
            "tagged": tagged,
            "already_tagged": already_tagged,
        }

    @staticmethod
    def _insert_keyword_hits(
        conn: Any,
        keyword_hits: Sequence[dict[str, Any]],
        url_to_id: dict[str, str],
    ) -> int:
        """Insert keyword hits into article_keyword_hits table.

        Args:
            conn: Database connection
            keyword_hits: List of {article_url, keyword, tag_name, filter_id, hit_field}
            url_to_id: Mapping of article URL -> article_id

        Returns:
            Number of rows inserted
        """
        now = datetime.now(timezone.utc).isoformat()
        inserted = 0
        for hit in keyword_hits:
            article_url = hit.get("article_url", "")
            article_id = url_to_id.get(article_url)
            if not article_id:
                continue
            try:
                conn.execute(
                    """
                    INSERT INTO article_keyword_hits
                        (article_id, keyword, tag_name, filter_id, hit_field, created_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (
                        article_id,
                        hit.get("keyword", ""),
                        hit.get("tag_name"),
                        hit.get("filter_id"),
                        hit.get("hit_field", "title+content"),
                        now,
                    ),
                )
                inserted += 1
            except Exception as exc:
                logger.warning(
                    "Failed to insert keyword hit for article %s: %s",
                    article_url, exc,
                )
        conn.commit()
        return inserted

    @staticmethod
    def _insert_hit_tags(
        conn: Any,
        keyword_hits: Sequence[dict[str, Any]],
        url_to_id: dict[str, str],
    ) -> int:
        """Insert only the tags that were actually hit into article_tags.

        v1.0.5.20: 替代 _copy_source_tags_to_article()，只插入命中的 tag。

        Args:
            conn: Database connection
            keyword_hits: List of {article_url, keyword, tag_name, ...}
            url_to_id: Mapping of article URL -> article_id

        Returns:
            Number of article_tags rows inserted
        """
        now = datetime.now(timezone.utc).isoformat()
        # 收集每个 article 命中的 tag（去重）
        article_hit_tags: dict[str, set[str]] = {}
        for hit in keyword_hits:
            article_url = hit.get("article_url", "")
            tag_name = hit.get("tag_name")
            article_id = url_to_id.get(article_url)
            if not article_id or not tag_name:
                continue
            if article_id not in article_hit_tags:
                article_hit_tags[article_id] = set()
            article_hit_tags[article_id].add(tag_name)

        inserted = 0
        for article_id, tags in article_hit_tags.items():
            for tag_name in tags:
                try:
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO article_tags
                            (article_id, tag_name, predicted, created_at)
                        VALUES (?, ?, 0, ?)
                        """,
                        (article_id, tag_name, now),
                    )
                    inserted += conn.execute("SELECT changes()").fetchone()[0]
                except Exception as exc:
                    logger.warning(
                        "Failed to insert article_tag %s/%s: %s",
                        article_id, tag_name, exc,
                    )
        conn.commit()
        return inserted

    def get_by_id(self, article_id: str) -> dict[str, Any] | None:
        """Return a full article dict by its id, or None if not found."""
        conn = get_db()
        row = conn.execute(
            "SELECT * FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_summary(
        self, article_id: str, summary: str, summary_at: str
    ) -> bool:
        """Write an LLM-generated summary back to the article."""
        conn = get_db()
        cur = conn.execute(
            """
            UPDATE articles
            SET summary = ?, summary_at = ?
            WHERE id = ?
            """,
            (summary, summary_at, article_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def update_fact_check(
        self,
        article_id: str,
        conclusion: str,
        method: str,
        checked_at: str,
        note: str | None = None,
    ) -> bool:
        """Write an LLM fact-check result back to the article."""
        conn = get_db()
        cur = conn.execute(
            """
            UPDATE articles
            SET fact_check_conclusion = ?,
                fact_check_method = ?,
                fact_checked_at = ?,
                fact_check_note = ?
            WHERE id = ?
            """,
            (conclusion, method, checked_at, note, article_id),
        )
        conn.commit()
        return cur.rowcount > 0

    def clean_all_html(self) -> dict[str, int]:
        """Retroactively clean HTML in ``raw_content`` for every article.

        Walks all articles whose ``raw_content`` contains a ``<`` (cheap
        heuristic for "has markup"), runs ``cleaner.extract_text`` on
        each, and writes the cleaned text back. Returns a stats dict
        with ``scanned``, ``updated``, ``unchanged``.

        Idempotent: re-running on already-cleaned text is a no-op (the
        cleaner output is deterministic and the equality check skips
        writes that wouldn't change anything).
        """
        # Local import: avoid pulling bs4 at module-import time.
        from ..fetch.cleaner import extract_text

        conn = get_db()
        rows = conn.execute(
            "SELECT id, raw_content FROM articles "
            "WHERE raw_content IS NOT NULL AND raw_content LIKE '%<%'"
        ).fetchall()
        pinned_ids = {
            r[0]
            for r in conn.execute("SELECT article_id FROM article_pins").fetchall()
        }

        scanned = 0
        updated = 0
        unchanged = 0
        touched_pinned: list[str] = []
        for row in rows:
            scanned += 1
            old = row["raw_content"] or ""
            new = extract_text(old)
            if new != old:
                conn.execute(
                    "UPDATE articles SET raw_content = ? WHERE id = ?",
                    (new, row["id"]),
                )
                if row["id"] in pinned_ids:
                    touched_pinned.append(row["id"])
                updated += 1
            else:
                unchanged += 1
        conn.commit()
        if touched_pinned:
            from moni.scoring import ensure_quality

            for aid in touched_pinned:
                ensure_quality(conn, aid)
        return {"scanned": scanned, "updated": updated, "unchanged": unchanged}
