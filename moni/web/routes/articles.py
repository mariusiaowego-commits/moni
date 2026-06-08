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
    """Convert a sqlite3.Row (dict-like) to an Article Pydantic model.

    v1.0.4: row now includes 'source' (TEXT, name) and 'tags' (comma-joined
    string from GROUP_CONCAT) populated by the SQL in list_articles /
    get_article.
    """
    tags_str = row.get("tags") or ""
    tags = [t.strip() for t in tags_str.split(",") if t.strip()]
    return Article(
        id=row["id"],
        source=row["source"],
        tags=tags,
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
    tag: str | None = Query(None, description="Filter by tag name"),
    # v1.0.5.10: keyword search — case-insensitive LIKE on title + raw_content.
    # Frontend has always sent this param (since v1.0.2), but the route
    # silently ignored it.  Now we actually filter.  The frontend
    # debounces 300ms; we use a parameterized LIKE here to avoid SQL
    # injection through the search box.
    keyword: str | None = Query(None, description="Substring match against title + content"),
) -> list[Article]:
    """List articles with optional source / tag / keyword filter.

    v1.0.4: LEFT JOIN article_tags + GROUP_CONCAT to surface tag names.
    Source filter is now a real WHERE clause (was a no-op before).

    v1.0.5.10: keyword is now a real WHERE clause (LIKE on title OR
    raw_content).  Previously the frontend sent it but the route
    silently dropped it on the floor, so the search box was a no-op.
    """
    conn = get_db()

    # Build WHERE + params dynamically. All three filters are independent.
    where_clauses: list[str] = []
    params: list[Any] = []
    if source:
        where_clauses.append("a.source = ?")
        params.append(source)
    if tag:
        where_clauses.append(
            "EXISTS (SELECT 1 FROM article_tags at "
            "WHERE at.article_id = a.id AND at.tag_name = ?)"
        )
        params.append(tag)
    if keyword:
        # SQLite LIKE is case-insensitive for ASCII by default; wrap
        # the user-supplied substring in %...% for substring match.
        where_clauses.append("(a.title LIKE ? OR a.raw_content LIKE ?)")
        like = f"%{keyword}%"
        params.append(like)
        params.append(like)
    where_sql = ("WHERE " + " AND ".join(where_clauses)) if where_clauses else ""

    sql = f"""
        SELECT a.id, a.title, a.url, a.source, a.publisher, a.published_at,
               a.raw_content, a.summary, a.fetched_at,
               (SELECT GROUP_CONCAT(at.tag_name, ',')
                  FROM article_tags at
                 WHERE at.article_id = a.id) AS tags
        FROM articles a
        {where_sql}
        ORDER BY a.published_at DESC, a.fetched_at DESC
    """
    rows = conn.execute(sql, params).fetchall()

    return [_row_to_article(dict(row)) for row in rows]


@router.get("/{article_id}", response_model=Article)
def get_article(article_id: str) -> Article:
    """Fetch a single article by its string ID."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT a.id, a.title, a.url, a.source, a.publisher, a.published_at,
               a.raw_content, a.summary, a.fetched_at,
               (SELECT GROUP_CONCAT(at.tag_name, ',')
                  FROM article_tags at
                 WHERE at.article_id = a.id) AS tags
        FROM articles a
        WHERE a.id = ?
        """,
        (article_id,),
    ).fetchone()

    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    return _row_to_article(dict(row))


@router.post("/{article_id}/summary", status_code=202)
def summarize_article(
    article_id: str,
    body: ArticleSummaryRequest,
) -> dict[str, Any]:
    """Enqueue a summary job for the given article (v1.0.5.1, async).

    Previously this route called ``hermes chat`` synchronously and
    returned the result in the response body, which blocked the request
    for up to 60s and surfaced a hard error on timeout.  Now the route
    enqueues a row in ``llm_jobs`` and returns ``202 Accepted`` with the
    job id.  A background worker thread (``moni.llm.jobs``) drains the
    queue and writes the LLM output back to ``articles.summary``.  The
    frontend polls ``/api/llm/jobs/{id}`` to follow progress.

    The active model is still resolved from ``llm_settings`` so the
    job is bound to the same model the synchronous path would have
    used.  We snapshot ``provider`` and ``model`` onto the job row so a
    later change to ``llm_settings`` doesn't redirect a queued job.
    """
    conn = get_db()

    # Confirm the article exists.  We deliberately check BEFORE resolving
    # the model so a missing article returns 404 (not 503) — closer to
    # what the user means.
    row = conn.execute(
        "SELECT id, title, raw_content, source, url, published_at FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    # Resolve active model.
    active_model_id = conn.execute(
        "SELECT value FROM llm_settings WHERE key = 'active_summary_model_id'"
    ).fetchone()
    if active_model_id is None or not active_model_id["value"]:
        raise ServiceUnavailableError(
            message="No active summary model configured",
            details={"hint": "set llm_settings.active_summary_model_id"},
        )
    model_row = conn.execute(
        """
        SELECT m.model_id, p.hermes_provider
        FROM llm_models m
        JOIN llm_providers p ON p.id = m.provider_id
        WHERE m.id = ?
        """,
        (active_model_id["value"],),
    ).fetchone()
    if model_row is None:
        raise ServiceUnavailableError(
            message=f"Active summary model {active_model_id['value']!r} not found",
        )

    from moni.llm.jobs import enqueue_job

    job_id = enqueue_job(
        task_type="summary",
        article_id=article_id,
        provider=model_row["hermes_provider"],
        model=model_row["model_id"],
    )
    return {
        "job_id": job_id,
        "status": "pending",
        "article_id": article_id,
        "task_type": "summary",
        "poll_url": f"/api/llm/jobs/{job_id}",
    }


@router.post("/{article_id}/fact-check", status_code=202)
def fact_check_article(
    article_id: str,
    body: FactCheckRequest,
) -> dict[str, Any]:
    """Enqueue a fact-check job for the given article (v1.0.5.1, async).

    Same async shape as ``/summary`` — returns 202 + job_id; the worker
    writes the conclusion back to ``articles.fact_check_conclusion``
    / ``fact_check_note`` / ``fact_checked_at``.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT id, title, raw_content, source, url, published_at FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    active_model_id = conn.execute(
        "SELECT value FROM llm_settings WHERE key = 'active_fact_check_model_id'"
    ).fetchone()
    if active_model_id is None or not active_model_id["value"]:
        raise ServiceUnavailableError(
            message="No active fact_check model configured",
            details={"hint": "set llm_settings.active_fact_check_model_id"},
        )
    model_row = conn.execute(
        """
        SELECT m.model_id, p.hermes_provider
        FROM llm_models m
        JOIN llm_providers p ON p.id = m.provider_id
        WHERE m.id = ?
        """,
        (active_model_id["value"],),
    ).fetchone()
    if model_row is None:
        raise ServiceUnavailableError(
            message=f"Active fact_check model {active_model_id['value']!r} not found",
        )

    from moni.llm.jobs import enqueue_job

    job_id = enqueue_job(
        task_type="fact_check",
        article_id=article_id,
        provider=model_row["hermes_provider"],
        model=model_row["model_id"],
    )
    return {
        "job_id": job_id,
        "status": "pending",
        "article_id": article_id,
        "task_type": "fact_check",
        "poll_url": f"/api/llm/jobs/{job_id}",
    }
