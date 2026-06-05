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

    def dedupe_insert(self, articles: Sequence[dict[str, Any]]) -> int:
        """Insert articles, skipping rows whose (source, url) already exist.

        Args:
            articles: Sequence of normalised article dicts.
                Required keys: ``title``, ``url``, ``source``.
                Optional keys: ``published_at``, ``content``, ``publisher``.

        Returns:
            Number of rows actually inserted (i.e. new articles, not duplicates).
        """
        if not articles:
            return 0

        conn = get_db()
        inserted = 0

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
                         fetched_at, raw_content)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
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
                    ),
                )
                # rowcount is 0 for IGNORE'd rows, 1 for inserted rows
                n = conn.execute("SELECT changes()").fetchone()[0]
                inserted += n
            except Exception as exc:
                logger.warning("Failed to insert article %s (%s): %s", url, source, exc)

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
