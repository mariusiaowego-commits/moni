"""routes/crud — article update, delete-preview and delete endpoints."""

from __future__ import annotations

import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.web.exceptions import BadRequestError, NotFoundError
from moni.web.models import Article, ArticleUpdate, DeletePreviewResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/articles", tags=["crud"])


def _row_to_article(row: dict) -> Article:
    return Article(
        id=row["id"],
        source=row["source"],
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


@router.put("/{article_id}", response_model=Article)
def update_article(article_id: str, body: ArticleUpdate) -> Article:
    """Update an article's title, publisher, published_at, raw_content and/or summary."""
    conn = get_db()

    updates: list[str] = []
    params: list = []

    if body.title is not None:
        updates.append("title = ?")
        params.append(body.title)
    if body.author is not None:
        updates.append("publisher = ?")
        params.append(body.author)
    if body.published_at is not None:
        updates.append("published_at = ?")
        params.append(
            body.published_at.isoformat()
            if hasattr(body.published_at, "isoformat")
            else body.published_at
        )
    if body.content is not None:
        updates.append("raw_content = ?")
        params.append(body.content)
    if body.summary is not None:
        updates.append("summary = ?")
        params.append(body.summary)

    if not updates:
        raise BadRequestError(message="No fields provided to update")

    cursor = conn.execute(
        f"UPDATE articles SET {', '.join(updates)} WHERE id = ?",
        [*params, article_id],
    )
    conn.commit()

    if cursor.rowcount == 0:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    row = conn.execute(
        """
        SELECT id, title, url, source, publisher, published_at,
               raw_content, summary, fetched_at
        FROM articles WHERE id = ?
        """,
        (article_id,),
    ).fetchone()

    return _row_to_article(dict(row))


@router.get("/{article_id}/delete-preview", response_model=DeletePreviewResponse)
def delete_preview(article_id: str) -> DeletePreviewResponse:
    """First step of the two-step delete: return a preview token.

    The token must be passed back via ?confirm=1 on the DELETE endpoint.
    This endpoint does NOT modify any data.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT id, title FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()

    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    token = secrets.token_urlsafe(16)
    expires_at = datetime.now(timezone.utc) + timedelta(minutes=10)

    return DeletePreviewResponse(
        token=token,
        article_title=row["title"],
        expires_at=expires_at,
    )


@router.delete("/{article_id}")
def delete_article(
    article_id: str,
    confirm: int = Query(0, description="Must be 1 to confirm deletion"),
) -> dict[str, str]:
    """Delete an article permanently.

    Requires ``confirm=1`` as a query parameter. Without it, returns 400.
    """
    if confirm != 1:
        raise BadRequestError(
            message="Deletion requires ?confirm=1 in the query string",
            details={"article_id": article_id},
        )

    conn = get_db()
    cursor = conn.execute(
        "DELETE FROM articles WHERE id = ?",
        (article_id,),
    )
    conn.commit()

    if cursor.rowcount == 0:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    return {"deleted": article_id}
