"""routes/articles — article listing, reading, summarisation and fact-check."""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.web.exceptions import NotFoundError, ServiceUnavailableError
from moni.web.models import (
    Article,
    ArticleSummaryRequest,
    FactCheckRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/articles", tags=["articles"])


def _row_to_article(row: dict[str, Any]) -> Article:
    """Convert a sqlite3.Row (dict-like) to an Article Pydantic model."""
    return Article(
        id=row["id"],
        source_id=0,  # legacy schema uses TEXT id; source_id not available
        title=row["title"],
        url=row["url"],
        author=row["publisher"],
        published_at=row["published_at"],
        content=row["raw_content"],
        summary=row["summary"],
        is_deleted=False,
        created_at=row["fetched_at"],
        updated_at=row["fetched_at"],
    )


@router.get("", response_model=list[Article])
def list_articles(
    source: str | None = Query(None, description="Filter by source name"),
    tag: str | None = Query(None, description="Filter by tag (reserved for future use)"),
) -> list[Article]:
    """List articles with optional source filter."""
    conn = get_db()
    if source:
        rows = conn.execute(
            """
            SELECT id, title, url, source, publisher, published_at,
                   raw_content, summary, fetched_at
            FROM articles
            WHERE source = ?
            ORDER BY published_at DESC, fetched_at DESC
            """,
            (source,),
        ).fetchall()
    else:
        rows = conn.execute(
            """
            SELECT id, title, url, source, publisher, published_at,
                   raw_content, summary, fetched_at
            FROM articles
            ORDER BY published_at DESC, fetched_at DESC
            """,
        ).fetchall()

    return [_row_to_article(dict(row)) for row in rows]


@router.get("/{article_id}", response_model=Article)
def get_article(article_id: str) -> Article:
    """Fetch a single article by its string ID."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, title, url, source, publisher, published_at,
               raw_content, summary, fetched_at
        FROM articles
        WHERE id = ?
        """,
        (article_id,),
    ).fetchone()

    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    return _row_to_article(dict(row))


@router.post("/{article_id}/summary")
def summarize_article(
    article_id: str,
    body: ArticleSummaryRequest,
) -> dict[str, Any]:
    """Generate a summary for an article using the LLM summarizer.

    Calls ``moni.summarizer`` if available; otherwise returns a 503.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT id, title, raw_content FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    try:
        from moni.summarizer import summarize

        article_dict = dict(row)
        result: str = summarize(article_dict)
        return {"summary": result}
    except ImportError:
        raise ServiceUnavailableError(
            message="Summarizer service is not available",
            details={"article_id": article_id},
        )


@router.post("/{article_id}/fact-check")
def fact_check_article(
    article_id: str,
    body: FactCheckRequest,
) -> dict[str, Any]:
    """Run a fact-check analysis on an article using the LLM fact-checker.

    Calls ``moni.factcheck`` if available; otherwise returns a 503.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT id, title, raw_content FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    try:
        from moni.factcheck import check

        article_dict = dict(row)
        result: dict[str, Any] = check(article_dict)
        return {"result": result}
    except ImportError:
        raise ServiceUnavailableError(
            message="Fact-check service is not available",
            details={"article_id": article_id},
        )
