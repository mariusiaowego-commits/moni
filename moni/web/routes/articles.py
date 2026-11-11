"""routes/articles — article listing, reading, summarisation and fact-check."""

from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.web.exceptions import BadRequestError, NotFoundError, ServiceUnavailableError
from moni.web.models import (
    Article,
    ArticleSummaryRequest,
    FactCheckRequest,
    ImportFulltextRequest,
    TagRef,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/articles", tags=["articles"])


def _row_to_article(row: dict[str, Any], fresh_thresholds: dict[str, str] | None = None) -> Article:
    """Convert a sqlite3.Row (dict-like) to an Article Pydantic model.

    v1.0.4: row includes 'source' (TEXT, name) and 'tags' (comma-joined
    string from GROUP_CONCAT).
    v1.0.5.12: each tag in the comma-joined string is "name" or
    "name|color" (color from tag_definitions.color).
    v1.0.5.14: format extended to "name|display_name|color".
    v1.0.5.20: row includes 'tag_keyword_hits' (JSON dict of
    {tag_name: [keywords]}). Each tag gets its own keyword_hits.
    v1.0.7.0: 新增 is_fresh 标志，基于 fetched_at 与该信源最新抓取时间比较。
    """
    # v1.0.5.20: parse tag_keyword_hits (JSON)
    tag_hits_str = row.get("tag_keyword_hits") or "{}"
    tag_hits: dict[str, list[str]] = {}
    try:
        tag_hits = json.loads(tag_hits_str) if tag_hits_str else {}
    except (json.JSONDecodeError, TypeError):
        pass

    tags_str = row.get("tags") or ""
    tags: list[TagRef] = []
    for raw in tags_str.split(","):
        raw = raw.strip()
        if not raw:
            continue
        parts = raw.split("|", 2)
        if len(parts) == 1:
            name, display_name, color = parts[0], None, None
        elif len(parts) == 2:
            name, display_name, color = parts[0], None, (parts[1].strip() or None)
        else:
            name, display_name, color = parts
            display_name = display_name.strip() or None
            color = color.strip() or None
        # v1.0.5.20: 给每个 tag 附上该 tag 下的命中关键词
        hits = tag_hits.get(name, [])
        tags.append(TagRef(name=name, display_name=display_name, color=color, keyword_hits=hits))

    # v1.0.7.0: 计算 is_fresh
    source = row["source"]
    fetched_at = row["fetched_at"]
    is_fresh = False
    if fresh_thresholds and source in fresh_thresholds:
        is_fresh = fetched_at == fresh_thresholds[source]

    # v1.0.8: pin status — p.pinned_at is NULL when the article isn't
    # pinned (LEFT JOIN).  Convert to (is_pinned, pinned_at).
    pinned_at_raw = row.get("pinned_at")
    is_pinned = pinned_at_raw is not None
    pinned_at_dt: datetime | None = None
    if pinned_at_raw:
        try:
            pinned_at_dt = datetime.fromisoformat(pinned_at_raw)
        except (ValueError, TypeError):
            pinned_at_dt = None

    return Article(
        id=row["id"],
        source=source,
        tags=tags,
        title=row["title"],
        url=row["url"],
        author=row["publisher"],
        published_at=row["published_at"],
        content=row["raw_content"],
        summary=row["summary"],
        is_deleted=False,
        is_fresh=is_fresh,
        is_pinned=is_pinned,
        pinned_at=pinned_at_dt,
        created_at=fetched_at,
        updated_at=fetched_at,
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
    # v1.0.5.14: limit/offset pagination.  Default 50 (matches the share-card-grid
    # screen height), max 200 to keep the JSON payload reasonable.
    # v1.0.5.15: sort by fetched_at DESC (入库时间降序).  Previously used
    # COALESCE(published_at, fetched_at) which made 36kr's freshly-published
    # articles dominate the list and hide anything the user just fetched
    # (wechat_rss / qbitai whose published_at is hours/days old).
    limit: int = Query(50, ge=1, le=200, description="Page size (1-200)"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> list[Article]:
    """List articles with optional source / tag / keyword filter.

    v1.0.4: LEFT JOIN article_tags + GROUP_CONCAT to surface tag names.
    Source filter is now a real WHERE clause (was a no-op before).

    v1.0.5.10: keyword is now a real WHERE clause (LIKE on title OR
    raw_content).  Previously the frontend sent it but the route
    silently dropped it on the floor, so the search box was a no-op.

    v1.0.5.14: limit/offset pagination.

    v1.0.5.15: sort by fetched_at DESC (入库时间降序, 刚抓的排最前).
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
               (SELECT GROUP_CONCAT(
                  CASE WHEN td.color IS NULL OR td.color = ''
                       THEN CASE WHEN td.display_name IS NULL OR td.display_name = ''
                                 THEN at.tag_name
                                 ELSE at.tag_name || '||'
                            END
                       ELSE CASE WHEN td.display_name IS NULL OR td.display_name = ''
                                 THEN at.tag_name || '|' || td.color
                                 ELSE at.tag_name || '|' || td.display_name || '|' || td.color
                            END
                  END, ',')
                  FROM article_tags at
                  LEFT JOIN tag_definitions td ON td.name = at.tag_name
                 WHERE at.article_id = a.id) AS tags,
               (SELECT '{{' || GROUP_CONCAT('"' || tag_name || '":[' || keywords || ']') || '}}'
                  FROM (SELECT tag_name, GROUP_CONCAT('"' || keyword || '"') as keywords
                          FROM article_keyword_hits
                         WHERE article_id = a.id
                         GROUP BY tag_name)) AS tag_keyword_hits,
               -- v1.0.8: pin status via LEFT JOIN.  p.pinned_at is NULL
               -- when the article is not pinned, so we use that as the
               -- boolean check.
               p.pinned_at AS pinned_at
        FROM articles a
        LEFT JOIN article_pins p ON p.article_id = a.id
        {where_sql}
        ORDER BY a.fetched_at DESC
        LIMIT ? OFFSET ?
    """
    params.extend([limit, offset])
    rows = conn.execute(sql, params).fetchall()

    # v1.0.7.0: 计算每个信源的最新抓取时间
    fresh_thresholds: dict[str, str] = {}
    threshold_rows = conn.execute(
        "SELECT source, MAX(fetched_at) as max_fetched FROM articles GROUP BY source"
    ).fetchall()
    for r in threshold_rows:
        fresh_thresholds[r["source"]] = r["max_fetched"]

    return [_row_to_article(dict(row), fresh_thresholds) for row in rows]


@router.get("/{article_id}", response_model=Article)
def get_article(article_id: str) -> Article:
    """Fetch a single article by its string ID."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT a.id, a.title, a.url, a.source, a.publisher, a.published_at,
               a.raw_content, a.summary, a.fetched_at,
               (SELECT GROUP_CONCAT(
                  CASE WHEN td.color IS NULL OR td.color = ''
                       THEN CASE WHEN td.display_name IS NULL OR td.display_name = ''
                                 THEN at.tag_name
                                 ELSE at.tag_name || '||'
                            END
                       ELSE CASE WHEN td.display_name IS NULL OR td.display_name = ''
                                 THEN at.tag_name || '|' || td.color
                                 ELSE at.tag_name || '|' || td.display_name || '|' || td.color
                            END
                  END, ',')
                  FROM article_tags at
                  LEFT JOIN tag_definitions td ON td.name = at.tag_name
                 WHERE at.article_id = a.id) AS tags,
               (SELECT '{{' || GROUP_CONCAT('"' || tag_name || '":[' || keywords || ']') || '}}'
                  FROM (SELECT tag_name, GROUP_CONCAT('"' || keyword || '"') as keywords
                          FROM article_keyword_hits
                         WHERE article_id = a.id
                         GROUP BY tag_name)) AS tag_keyword_hits,
               p.pinned_at AS pinned_at
        FROM articles a
        LEFT JOIN article_pins p ON p.article_id = a.id
        WHERE a.id = ?
        """,
        (article_id,),
    ).fetchone()

    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    # v1.0.7.0: 计算该信源的最新抓取时间
    fresh_thresholds: dict[str, str] = {}
    threshold_rows = conn.execute(
        "SELECT source, MAX(fetched_at) as max_fetched FROM articles GROUP BY source"
    ).fetchall()
    for r in threshold_rows:
        fresh_thresholds[r["source"]] = r["max_fetched"]

    return _row_to_article(dict(row), fresh_thresholds)


def _resolve_job_model(
    conn, setting_key: str, label: str
) -> tuple[str, str]:
    """Resolve (hermes_provider, model_id) for a queued LLM job.

    ``llm_model_mode=profile`` 时模型由 hermes 的 moni profile 决定（即 magpie
    里选的路由组），不依赖 ``llm_settings.active_*`` 与 ``llm_models`` 行；
    返回空串，由 ``enqueue_job`` 记成 ``provider='profile'``。
    """
    from moni.llm.runner import read_model_mode

    if read_model_mode() == "profile":
        return "", ""

    active_model_id = conn.execute(
        "SELECT value FROM llm_settings WHERE key = ?", (setting_key,)
    ).fetchone()
    if active_model_id is None or not active_model_id["value"]:
        raise ServiceUnavailableError(
            message=f"No active {label} model configured",
            details={"hint": f"set llm_settings.{setting_key}"},
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
            message=f"Active {label} model {active_model_id['value']!r} not found",
        )
    return model_row["hermes_provider"], model_row["model_id"]


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

    Model resolution depends on ``llm_settings.llm_model_mode``:
    ``explicit`` resolves ``active_summary_model_id`` and snapshots
    ``provider``/``model`` onto the job row; ``profile`` leaves both to
    the hermes ``moni`` profile (magpie routing group).
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

    provider, model = _resolve_job_model(conn, "active_summary_model_id", "summary")

    from moni.llm.jobs import enqueue_job

    job_id = enqueue_job(
        task_type="summary",
        article_id=article_id,
        provider=provider,
        model=model,
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

    provider, model = _resolve_job_model(
        conn, "active_fact_check_model_id", "fact_check"
    )

    from moni.llm.jobs import enqueue_job

    job_id = enqueue_job(
        task_type="fact_check",
        article_id=article_id,
        provider=provider,
        model=model,
    )
    return {
        "job_id": job_id,
        "status": "pending",
        "article_id": article_id,
        "task_type": "fact_check",
        "poll_url": f"/api/llm/jobs/{job_id}",
    }


# ── import full-text (v1.0.5.24) ─────────────────────────────────────────


def _resolve_user_home() -> Path:
    """Resolve the user home directory, preferring pwd entry over $HOME."""
    try:
        import pwd

        return Path(pwd.getpwuid(os.getuid()).pw_dir)
    except (ImportError, KeyError, OSError):
        return Path.home()


@router.post("/{article_id}/import-fulltext")
def import_fulltext(article_id: str, body: ImportFulltextRequest) -> dict[str, Any]:
    """Import full-text content from a local .md file into the article.

    Reads the file, validates it's a .md file that exists, and updates
    ``articles.raw_content`` with the file content.  This enables LLM
    summary / fact-check on articles that originally only had a snippet.
    """
    conn = get_db()

    # Verify article exists
    row = conn.execute("SELECT id FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    # Validate file
    file_path = Path(body.file_path).expanduser()
    if not file_path.exists():
        raise NotFoundError(message=f"File not found: {body.file_path}")
    if file_path.suffix.lower() not in (".md", ".markdown", ".txt"):
        raise BadRequestError(
            message=f"Only .md/.txt files are supported, got: {file_path.suffix}",
        )

    # Security: only allow files under common directories
    home = _resolve_user_home()
    allowed_prefixes = [
        home / "Documents",
        home / "Desktop",
        home / "Downloads",
        home / "Library" / "Mobile Documents",
    ]
    if not any(str(file_path).startswith(str(p)) for p in allowed_prefixes):
        raise BadRequestError(
            message=f"File must be under ~/Documents, ~/Desktop, ~/Downloads, or ~/Library/Mobile Documents",
        )

    try:
        content = file_path.read_text(encoding="utf-8")
    except Exception as exc:
        raise BadRequestError(message=f"Failed to read file: {exc}")

    if not content.strip():
        raise BadRequestError(message="File is empty")

    # Update article content
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE articles SET raw_content = ?, summary = NULL, summary_at = NULL WHERE id = ?",
        (content, article_id),
    )
    conn.commit()

    logger.info("Imported full-text for article %s from %s (%d chars)", article_id, file_path, len(content))

    return {
        "article_id": article_id,
        "file_path": str(file_path),
        "char_count": len(content),
        "content_preview": content[:500],
    }
