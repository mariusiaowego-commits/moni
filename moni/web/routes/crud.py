"""routes/crud — article create, update, delete-preview and delete endpoints."""

from __future__ import annotations

import logging
import secrets
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.web.exceptions import BadRequestError, NotFoundError
from moni.web.models import Article, ArticleCreate, ArticleUpdate, DeletePreviewResponse

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


@router.post("", response_model=Article, status_code=201)
def create_article(body: ArticleCreate) -> Article:
    """Create a new article manually (v1.0.5.10).

    Used by the CRUD UI's "+ 新建文章" button.  The ``source_id`` field
    on the model is actually the source's ``name`` (not the row id)
    because ``articles.source`` is a TEXT FK-by-name to ``sources.name``.
    Returns 201 with the created row.

    INSERT OR IGNORE on (source, url) — duplicate submissions (same
    source+url) return the existing row instead of erroring.
    """
    conn = get_db()

    # Validate the source name exists.  We don't want to insert
    # articles that reference a non-existent source.
    src = conn.execute(
        "SELECT id, name FROM sources WHERE name = ?",
        (body.source_id,),
    ).fetchone()
    if src is None:
        raise BadRequestError(
            message=f"Unknown source: {body.source_id!r}",
            details={"source_id": body.source_id,
                     "hint": "use the source name (e.g. '36kr_rss'), not the row id"},
        )

    # Use a UUID-ish id so the row is unique even without DB-side default
    article_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()

    # body.published_at is Optional[datetime]; normalise to ISO string or None
    published_at = None
    if body.published_at is not None:
        published_at = body.published_at.isoformat()

    from moni.fetch.content import apply_cleaning

    art: dict = {"title": body.title, "content": body.content}
    apply_cleaning(art)

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO articles
            (id, title, url, source, publisher, published_at, fetched_at,
             raw_content, content_state, content_chars, content_reasons,
             content_cleaner, content_cleaned_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            article_id,
            body.title,
            body.url,
            body.source_id,
            body.author,
            published_at,
            now,
            art["content"],
            art["content_state"],
            art["content_chars"],
            art["content_reasons"],
            art["content_cleaner"],
            art["content_cleaned_at"],
        ),
    )
    conn.commit()

    # If the INSERT was ignored due to UNIQUE(source,url), cursor.rowcount
    # is 0; fall through to re-read the existing row.
    if cursor.rowcount == 0:
        row = conn.execute(
            "SELECT id FROM articles WHERE source = ? AND url = ?",
            (body.source_id, body.url),
        ).fetchone()
        article_id = row["id"]

    # Re-read so we return whatever DB defaults fired.
    row = conn.execute(
        """
        SELECT id, title, url, source, publisher, published_at,
               raw_content, summary, fetched_at
        FROM articles WHERE id = ?
        """,
        (article_id,),
    ).fetchone()
    return _row_to_article(dict(row))


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
        from moni.fetch.content import clean_for_write

        row = conn.execute(
            "SELECT raw_content FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(message=f"Article {article_id!r} not found")

        old_text = row["raw_content"] or ""

        art: dict = {"title": body.title or "", "content": body.content}
        if clean_for_write(art, old_text) is None:
            raise BadRequestError(
                message="导入的正文清洗后为空，已拒绝写入；请检查文件内容",
            )
        updates += [
            "raw_content = ?",
            "content_state = ?",
            "content_chars = ?",
            "content_reasons = ?",
            "content_cleaner = ?",
            "content_cleaned_at = ?",
        ]
        params += [
            art["content"],
            art["content_state"],
            art["content_chars"],
            art["content_reasons"],
            art["content_cleaner"],
            art["content_cleaned_at"],
        ]
        if art["content"] != old_text:
            # Only a real body change invalidates the derived text products;
            # re-importing the same file must not wipe summaries and briefs.
            updates += [
                "summary = NULL",
                "summary_at = NULL",
                "ai_summary = NULL",
                "ai_summary_at = NULL",
                "has_ai_summary = 0",
                "brief_summary = NULL",
                "brief_summary_at = NULL",
                "brief_body_md = NULL",
                "brief_body_at = NULL",
            ]
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

    # Pin quality derives from body/publisher/published_at (§5.2.5).
    if body.author is not None or body.published_at is not None or body.content is not None:
        from moni.scoring import recompute_quality_if_pinned

        recompute_quality_if_pinned(conn, article_id)

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
