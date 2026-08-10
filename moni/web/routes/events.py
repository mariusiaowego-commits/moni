"""routes/events — 行业事件日历 API。

v1.0.16: 行业事件日历后端。
- GET /api/events — 按 year/month/event_type/subject/review_status 筛选 + 分页
- GET /api/events/by-article/{article_id}
- POST /api/events/retry/{article_id} — 手动重试
- GET /api/events/pending — date_precision in (relative, unknown)
- GET /api/events/stats — 统计数据
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.events.repository import EventRepository
from moni.web.exceptions import NotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/events", tags=["events"])


def _row_to_event(row: dict[str, Any]) -> dict[str, Any]:
    """格式化事件行，加上文章关联信息。"""
    return {
        "event_id": row.get("event_id"),
        "article_id": row.get("article_id"),
        "article_title": row.get("article_title"),
        "article_source": row.get("article_source"),
        "article_url": row.get("article_url"),
        "article_published_at": row.get("article_published_at"),
        "event_date": row.get("event_date"),
        "event_date_precision": row.get("event_date_precision"),
        "subject": row.get("subject"),
        "subject_raw": row.get("subject_raw"),
        "event_type": row.get("event_type"),
        "summary": row.get("summary"),
        "extraction_confidence": row.get("extraction_confidence"),
        "review_status": row.get("review_status"),
        "extraction_status": row.get("extraction_status"),
        "extraction_attempts": row.get("extraction_attempts"),
        "last_error": row.get("last_error"),
        "extracted_at": row.get("extracted_at"),
        "evidence_snippet": row.get("evidence_snippet"),
        "normalize_version": row.get("normalize_version"),
        "created_at": row.get("created_at"),
    }


@router.get("")
def list_events(
    year: int | None = Query(None, description="Filter by year"),
    month: int | None = Query(None, description="Filter by month (1-12), requires year"),
    event_type: str | None = Query(None, description="Filter by event_type: regulation/product/financing/tech/other"),
    subject: str | None = Query(None, description="Filter by subject (fuzzy match)"),
    review_status: str | None = Query(None, description="Filter by review_status: pending/confirmed/rejected"),
    limit: int = Query(50, ge=1, le=200, description="Page size (1-200)"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> dict[str, Any]:
    """事件列表（带筛选 + 分页）。"""
    events = EventRepository.list_events(
        year=year,
        month=month,
        event_type=event_type,
        subject=subject,
        review_status=review_status,
        limit=limit,
        offset=offset,
    )
    total = EventRepository.count_events(
        year=year,
        month=month,
        event_type=event_type,
        subject=subject,
        review_status=review_status,
    )
    return {
        "items": [_row_to_event(e) for e in events],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/by-article/{article_id}")
def get_events_by_article(article_id: str) -> dict[str, Any]:
    """获取某篇文章的所有事件。"""
    # 验证文章存在
    conn = get_db()
    art_row = conn.execute("SELECT id FROM articles WHERE id = ?", (article_id,)).fetchone()
    if art_row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    events = EventRepository.get_by_article(article_id)
    return {
        "article_id": article_id,
        "items": [_row_to_event(e) for e in events],
        "count": len(events),
    }


@router.post("/retry/{article_id}")
def retry_extraction(article_id: str) -> dict[str, Any]:
    """手动重试某篇文章的事件提取。

    清除该文章的所有现有事件，标记为 pending，然后同步重新提取。
    注意：同步调用会阻塞请求，仅用于手动重试（单篇文章）。
    """
    from moni.events.pipeline import extract_events_for_article

    conn = get_db()
    art_row = conn.execute(
        "SELECT id, title, raw_content, source, published_at FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if art_row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    # 删除已有事件（重新提取）
    conn.execute("DELETE FROM events WHERE article_id = ?", (article_id,))
    conn.commit()

    # 同步重新提取
    result = extract_events_for_article(dict(art_row))

    return {
        "article_id": article_id,
        "status": result["status"],
        "event_count": result["event_count"],
        "error": result.get("error"),
    }


@router.get("/pending")
def list_pending_review(
    limit: int = Query(50, ge=1, le=200, description="Page size"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> dict[str, Any]:
    """待人工确认的事件列表（date_precision 为 relative/unknown）。"""
    events = EventRepository.list_pending_review(limit=limit, offset=offset)
    return {
        "items": [_row_to_event(e) for e in events],
        "limit": limit,
        "offset": offset,
    }


@router.post("/{event_id}/confirm")
def confirm_event(event_id: str) -> dict[str, Any]:
    """人工确认事件（review_status → confirmed, P2-2 v1.0.20）。"""
    if not EventRepository.set_review_status(event_id, "confirmed"):
        raise NotFoundError(message=f"Event {event_id!r} not found")
    return {"event_id": event_id, "review_status": "confirmed"}


@router.post("/{event_id}/reject")
def reject_event(event_id: str) -> dict[str, Any]:
    """人工驳回事件（review_status → rejected, P2-2 v1.0.20）。"""
    if not EventRepository.set_review_status(event_id, "rejected"):
        raise NotFoundError(message=f"Event {event_id!r} not found")
    return {"event_id": event_id, "review_status": "rejected"}


@router.get("/stats")
def get_stats() -> dict[str, Any]:
    """事件统计数据。"""
    return EventRepository.stats()
