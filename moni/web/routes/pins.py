"""routes/pins — article pin / unpin (v1.0.8).

User-initiated flag separate from basket (which is system-passive).
Schema: ``article_pins(article_id PK, pinned_at)`` — see migration 017.

Endpoints
---------
* ``POST   /api/articles/{id}/pin?on=true|false``  — toggle (or explicit set)
* ``DELETE /api/articles/{id}/pin``               — explicit unpin
* ``GET    /api/articles/pinned``                  — list pinned, with filters
* ``POST   /api/articles/pins/bulk-unpin``         — bulk unpin, returns ids

Design notes
------------
* Toggle vs explicit: ``POST ?on=true|false`` is idempotent and
  "RESTful-natural"; clients can use either form.  ``DELETE`` is
  the strict unpin.
* GET /pinned filters:
  - ``sources`` (CSV) — multi-select union (``a.source IN (...)``)
  - ``tags``    (CSV) — multi-select union (any article with any of
    the listed tags is included)
  - ``sort``    — ``pinned_at_desc`` (default) / ``pinned_at_asc`` /
    ``title_asc`` / ``title_desc``
  - ``page`` / ``page_size`` — 1-200
* Single-user local project; no ``user_id`` column.  If we ever
  add multi-user, add a ``user_id`` column to ``article_pins``.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel

from moni.db.connection import get_db
from moni.scoring import ensure_quality, needs_quality_refresh
from moni.web.exceptions import NotFoundError

router = APIRouter(prefix="/api/articles", tags=["pins"])


# ── Pydantic models ────────────────────────────────────────────────────────


class PinToggleResponse(BaseModel):
    article_id: str
    is_pinned: bool
    pinned_at: datetime | None
    quality_score: int | None = None
    quality_status: str | None = None
    quality_error: str | None = None
    quality_formula: str | None = None
    quality_breakdown: dict | None = None
    quality_score_at: str | None = None


class BulkUnpinRequest(BaseModel):
    article_ids: list[str]


class BulkUnpinResponse(BaseModel):
    unpinned: list[str]
    not_found: list[str]


class QualityRefreshRequest(BaseModel):
    article_ids: list[str]


class QualityRefreshResponse(BaseModel):
    updated: list[str]
    unchanged: list[str]
    skipped: list[str]


# ── POST /api/articles/{id}/pin?on=true|false ──────────────────────────────


@router.post("/{article_id}/pin", response_model=PinToggleResponse)
def toggle_pin(
    article_id: str,
    on: bool = Query(True, description="true=pin, false=unpin"),
) -> PinToggleResponse:
    """Pin (or unpin) an article.  Idempotent.

    v1.0.8: replaces the absence of any pin feature.  Uses
    ``INSERT OR REPLACE`` so re-pinning an already-pinned article
    refreshes ``pinned_at`` to "now" — useful when the user
    "re-pins" to bump an item to the top of the pin list.
    """
    conn = get_db()
    # Verify the article exists — CASCADE would let us skip this
    # but the user should know immediately if they typo'd an id.
    exists = conn.execute(
        "SELECT 1 FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    if not exists:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    now = datetime.now(timezone.utc).isoformat()
    if on:
        # INSERT OR REPLACE refreshes pinned_at on re-pin.
        conn.execute(
            "INSERT OR REPLACE INTO article_pins (article_id, pinned_at) "
            "VALUES (?, ?)",
            (article_id, now),
        )
    else:
        conn.execute(
            "DELETE FROM article_pins WHERE article_id = ?", (article_id,)
        )
    conn.commit()
    quality: dict[str, Any] = {}
    if on:
        result = ensure_quality(conn, article_id)
        if result is not None:
            quality = {
                "quality_score": result.score,
                "quality_status": result.status,
                "quality_error": result.error,
                "quality_formula": result.formula,
                "quality_breakdown": result.breakdown,
                "quality_score_at": now,
            }
    return PinToggleResponse(
        article_id=article_id,
        is_pinned=on,
        pinned_at=datetime.fromisoformat(now) if on else None,
        **quality,
    )


# ── DELETE /api/articles/{id}/pin ─────────────────────────────────────────


@router.delete("/{article_id}/pin", response_model=PinToggleResponse)
def delete_pin(article_id: str) -> PinToggleResponse:
    """Explicit unpin.  Idempotent — unpinning a non-pinned article
    is a no-op (returns is_pinned=False)."""
    conn = get_db()
    conn.execute(
        "DELETE FROM article_pins WHERE article_id = ?", (article_id,)
    )
    conn.commit()
    return PinToggleResponse(article_id=article_id, is_pinned=False, pinned_at=None)


# ── GET /api/articles/pinned ──────────────────────────────────────────────


@router.get("/pinned")
def list_pinned_articles(
    sources: str | None = Query(
        None,
        description=(
            "Comma-separated source names for multi-select union.  "
            "e.g. 'mpaypass,qbitai' means mpaypass UNION qbitai."
        ),
    ),
    tags: str | None = Query(
        None,
        description=(
            "Comma-separated tag names for multi-select union.  An "
            "article is included if it has ANY of the listed tags."
        ),
    ),
    sort: str = Query(
        "pinned_at_desc",
        description=(
            "Sort order: pinned_at_desc (default) | pinned_at_asc | "
            "title_asc | title_desc"
        ),
    ),
    page: int = Query(1, ge=1, description="1-indexed page number"),
    page_size: int = Query(50, ge=1, le=200, description="Page size (1-200)"),
) -> dict[str, Any]:
    """List pinned articles, with source/tag/sort/pagination filters.

    v1.0.8: returns the same shape as ``/api/articles`` (list of
    Article-shaped dicts) plus a ``total`` count for pagination
    metadata.  Uses LEFT JOIN onto article_tags so we can filter by
    tag without a subquery.
    """
    conn = get_db()

    # Build WHERE clauses incrementally.
    where_clauses: list[str] = ["p.article_id IS NOT NULL"]
    params: list[Any] = []

    if sources:
        source_list = [s.strip() for s in sources.split(",") if s.strip()]
        if source_list:
            placeholders = ",".join("?" * len(source_list))
            where_clauses.append(f"a.source IN ({placeholders})")
            params.extend(source_list)

    if tags:
        tag_list = [t.strip() for t in tags.split(",") if t.strip()]
        if tag_list:
            tag_placeholders = ",".join("?" * len(tag_list))
            where_clauses.append(
                f"EXISTS (SELECT 1 FROM article_tags at "
                f"WHERE at.article_id = a.id AND at.tag_name IN ({tag_placeholders}))"
            )
            params.extend(tag_list)

    where_sql = "WHERE " + " AND ".join(where_clauses)

    # Map sort param to SQL.
    sort_map = {
        "pinned_at_desc": "p.pinned_at DESC",
        "pinned_at_asc":  "p.pinned_at ASC",
        "title_asc":      "a.title COLLATE NOCASE ASC",
        "title_desc":     "a.title COLLATE NOCASE DESC",
    }
    order_sql = sort_map.get(sort, "p.pinned_at DESC")

    # Total count for pagination metadata.
    total = conn.execute(
        f"SELECT COUNT(*) AS n FROM articles a "
        f"INNER JOIN article_pins p ON p.article_id = a.id "
        f"{where_sql}",
        params,
    ).fetchone()["n"]

    # Paginated fetch.
    offset = (page - 1) * page_size
    rows = conn.execute(
        f"""
        SELECT a.id, a.title, a.url, a.source, a.publisher, a.published_at,
               a.raw_content, a.summary, a.fetched_at,
               a.quality_score, a.quality_status, a.quality_error,
               a.quality_formula, a.quality_breakdown_json,
               a.quality_input_hash, a.quality_score_at,
               p.pinned_at AS pinned_at,
               EXISTS (SELECT 1 FROM reader_conversations rc
                       WHERE rc.article_id = a.id AND rc.unread = 1) AS reader_unread,
               EXISTS (SELECT 1 FROM reader_conversations rc
                       WHERE rc.article_id = a.id) AS has_reader_chat,
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
                         GROUP BY tag_name)) AS tag_keyword_hits
        FROM articles a
        INNER JOIN article_pins p ON p.article_id = a.id
        {where_sql}
        ORDER BY {order_sql}
        LIMIT ? OFFSET ?
        """,
        params + [page_size, offset],
    ).fetchall()

    # Reuse the existing _row_to_article for shape consistency.  We
    # can't import from articles.py (would create a circular
    # import: articles.py imports this module's router would be
    # cleaner via FastAPI's include_router; but for shape conversion
    # we want the same code).  Easiest fix: copy the row-shape
    # fields manually here.
    from moni.web.routes.articles import _row_to_article
    items = []
    for row in rows:
        item = _row_to_article(dict(row)).model_dump()
        item["quality"] = quality_payload(row)
        items.append(item)

    return {
        "items": items,
        "total": total,
        "page": page,
        "page_size": page_size,
        "sort": sort,
    }

def quality_payload(row: Any) -> dict[str, Any]:
    """Shape one row's stored quality for the Pin client."""
    breakdown = None
    raw = row["quality_breakdown_json"]
    if raw:
        try:
            breakdown = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            breakdown = None
    return {
        "score": row["quality_score"],
        "status": row["quality_status"],
        "error": row["quality_error"],
        "formula": row["quality_formula"],
        "breakdown": breakdown,
        "score_at": row["quality_score_at"],
    }


# ── POST /api/articles/pins/quality-refresh ───────────────────────────────


@router.post("/pins/quality-refresh", response_model=QualityRefreshResponse)
def refresh_pin_quality(body: QualityRefreshRequest) -> QualityRefreshResponse:
    """Recompute Pin quality for the given ids.

    GET /pinned stays read-only; the Pin page calls this only for rows whose
    ``quality_formula`` is missing or outdated.  Ids outside the pin set are
    skipped rather than scored.
    """
    updated: list[str] = []
    unchanged: list[str] = []
    skipped: list[str] = []
    if not body.article_ids:
        return QualityRefreshResponse(updated=[], unchanged=[], skipped=[])
    conn = get_db()
    ids = body.article_ids[:200]
    placeholders = ",".join("?" * len(ids))
    rows = conn.execute(
        f"SELECT a.id, a.title, a.raw_content, a.publisher, a.published_at, "
        f"a.source, a.ai_summary, a.fact_check_conclusion, a.url, "
        f"a.quality_formula, a.quality_status, a.quality_breakdown_json, "
        f"a.quality_input_hash "
        f"FROM articles a INNER JOIN article_pins p ON p.article_id = a.id "
        f"WHERE a.id IN ({placeholders})",
        ids,
    ).fetchall()
    pinned = {row["id"]: row for row in rows}
    for article_id in ids:
        row = pinned.get(article_id)
        if row is None:
            skipped.append(article_id)
            continue
        if not needs_quality_refresh(row):
            unchanged.append(article_id)
            continue
        if ensure_quality(conn, article_id) is not None:
            updated.append(article_id)
        else:
            skipped.append(article_id)
    return QualityRefreshResponse(updated=updated, unchanged=unchanged, skipped=skipped)


# ── POST /api/articles/pins/bulk-unpin ─────────────────────────────────────


@router.post("/pins/bulk-unpin", response_model=BulkUnpinResponse)
def bulk_unpin(body: BulkUnpinRequest) -> BulkUnpinResponse:
    """Unpin many articles at once.  Returns the list of article_ids
    that were actually unpinned (i.e. were in the pin set) and a
    separate list of ids that were not found in the pin set.

    v1.0.8: used by the Pin tab's batch unpin action.  The 5-second
    undo UX in the frontend means this is only called after the
    undo window expires, not on every checkbox toggle.
    """
    if not body.article_ids:
        return BulkUnpinResponse(unpinned=[], not_found=[])
    conn = get_db()
    # Check which ids are actually pinned.
    placeholders = ",".join("?" * len(body.article_ids))
    pinned_rows = conn.execute(
        f"SELECT article_id FROM article_pins WHERE article_id IN ({placeholders})",
        body.article_ids,
    ).fetchall()
    pinned_set = {row["article_id"] for row in pinned_rows}
    # DELETE in one shot — only the pinned ones get touched.
    if pinned_set:
        del_placeholders = ",".join("?" * len(pinned_set))
        conn.execute(
            f"DELETE FROM article_pins WHERE article_id IN ({del_placeholders})",
            list(pinned_set),
        )
        conn.commit()
    not_found = [aid for aid in body.article_ids if aid not in pinned_set]
    return BulkUnpinResponse(unpinned=sorted(pinned_set), not_found=sorted(not_found))
