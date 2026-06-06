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
) -> list[Article]:
    """List articles with optional source / tag filter.

    v1.0.4: LEFT JOIN article_tags + GROUP_CONCAT to surface tag names.
    Source filter is now a real WHERE clause (was a no-op before).
    """
    conn = get_db()

    # Build WHERE + params dynamically. Source/tag filters are independent.
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


@router.post("/{article_id}/summary")
def summarize_article(
    article_id: str,
    body: ArticleSummaryRequest,
) -> dict[str, Any]:
    """Generate a summary for an article via the LLM (薄壳模式).

    Reads the active summary model from ``llm_settings`` and dispatches
    via ``moni.llm.call_llm`` (which subprocess-calls ``hermes chat``).
    Writes the result back to ``articles.summary`` + ``summary_at``.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT id, title, raw_content, source, url, published_at FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    article_dict = dict(row)

    # Resolve active model from llm_settings
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

    from moni.llm import call_llm
    from moni.llm.prompts import render_summary

    prompt = render_summary(article_dict)
    try:
        summary_text = call_llm(model_row["hermes_provider"], model_row["model_id"], prompt)
    except RuntimeError as exc:
        raise ServiceUnavailableError(
            message=f"LLM call failed: {exc}",
        )

    # Persist
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE articles SET summary = ?, summary_at = ? WHERE id = ?",
        (summary_text, now, article_id),
    )
    conn.commit()

    return {"summary": summary_text, "summary_at": now}


@router.post("/{article_id}/fact-check")
def fact_check_article(
    article_id: str,
    body: FactCheckRequest,
) -> dict[str, Any]:
    """Run a fact-check analysis on an article via the LLM (薄壳模式).

    Reads the active fact-check model from ``llm_settings`` and dispatches
    via ``moni.llm.call_llm_json`` (parses ```json``` fence). Writes the
    result back to ``articles.fact_check_*`` fields.
    """
    conn = get_db()
    row = conn.execute(
        "SELECT id, title, raw_content, source, url, published_at FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    article_dict = dict(row)

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

    from moni.llm import call_llm_json
    from moni.llm.prompts import render_fact_check

    prompt = render_fact_check(article_dict)
    try:
        result_obj = call_llm_json(
            model_row["hermes_provider"], model_row["model_id"], prompt
        )
    except RuntimeError as exc:
        raise ServiceUnavailableError(message=f"LLM call failed: {exc}")

    # Persist
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    conclusion = result_obj.get("conclusion") or result_obj.get("result") or ""
    note = result_obj.get("note") or result_obj.get("details") or ""
    conn.execute(
        """
        UPDATE articles
        SET fact_check_conclusion = ?,
            fact_check_method    = 'llm_inference',
            fact_checked_at       = ?,
            fact_check_note       = ?
        WHERE id = ?
        """,
        (str(conclusion)[:200], now, str(note)[:2000], article_id),
    )
    conn.commit()

    return {
        "conclusion": conclusion,
        "note": note,
        "checked_at": now,
        "method": "llm_inference",
    }
