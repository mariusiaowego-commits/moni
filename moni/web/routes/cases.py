"""routes/cases — F6 同业案例库 API。

v1.0.17: 同业案例库后端。
- GET /api/cases — 按 case_type/case_action/subject/reference_value/review_status/case_lifecycle 筛选 + 分页
- GET /api/cases/{case_id}
- GET /api/cases/by-article/{article_id}
- POST /api/cases/retry/{article_id} — 手动重试
- GET /api/cases/pending — 待人工 review
- GET /api/cases/stats — 统计数据
- GET /api/cases/search?q=... — FTS5 全文检索 (trigram)
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.cases.repository import CaseRepository
from moni.web.exceptions import NotFoundError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cases", tags=["cases"])


def _row_to_case(row: dict[str, Any]) -> dict[str, Any]:
    """格式化案例行, 加上文章关联信息."""
    return {
        "case_id": row.get("case_id"),
        "article_id": row.get("article_id"),
        "article_title": row.get("article_title"),
        "article_source": row.get("article_source"),
        "article_url": row.get("article_url"),
        "article_published_at": row.get("article_published_at"),
        "case_date": row.get("case_date"),
        "case_date_precision": row.get("case_date_precision"),
        "subject": row.get("subject"),
        "subject_raw": row.get("subject_raw"),
        "case_type": row.get("case_type"),
        "case_action": row.get("case_action"),
        "counterparty": row.get("counterparty"),
        "amount": row.get("amount"),
        "reference_value": row.get("reference_value"),
        "reference_rationale": row.get("reference_rationale"),
        "summary": row.get("summary"),
        "evidence_snippet": row.get("evidence_snippet"),
        "extraction_confidence": row.get("extraction_confidence"),
        "extraction_status": row.get("extraction_status"),
        "case_lifecycle": row.get("case_lifecycle"),
        "review_status": row.get("review_status"),
        "extraction_attempts": row.get("extraction_attempts"),
        "last_error": row.get("last_error"),
        "extracted_at": row.get("extracted_at"),
        "normalize_version": row.get("normalize_version"),
        "created_at": row.get("created_at"),
        "updated_at": row.get("updated_at"),
    }


@router.get("")
def list_cases(
    case_type: str | None = Query(None, description="Filter by case_type: AI/PI"),
    case_action: str | None = Query(None, description="Filter by case_action: launch/pilot/scale/transform/exit"),
    subject: str | None = Query(None, description="Filter by subject (fuzzy match)"),
    reference_value: str | None = Query(None, description="Filter by reference_value: high/medium/low"),
    review_status: str | None = Query(None, description="Filter by review_status: pending/confirmed/rejected"),
    case_lifecycle: str | None = Query(None, description="Filter by case_lifecycle: draft/verified/published/deprecated/archived"),
    limit: int = Query(50, ge=1, le=200, description="Page size (1-200)"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> dict[str, Any]:
    """案例列表 (带筛选 + 分页)."""
    cases = CaseRepository.list_cases(
        case_type=case_type,
        case_action=case_action,
        subject=subject,
        reference_value=reference_value,
        review_status=review_status,
        case_lifecycle=case_lifecycle,
        limit=limit,
        offset=offset,
    )
    total = CaseRepository.count_cases(
        case_type=case_type,
        case_action=case_action,
        subject=subject,
        reference_value=reference_value,
        review_status=review_status,
        case_lifecycle=case_lifecycle,
    )
    return {
        "items": [_row_to_case(c) for c in cases],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/search")
def search_cases(
    q: str = Query(..., min_length=1, description="搜索关键词"),
    limit: int = Query(50, ge=1, le=200, description="返回数量上限"),
) -> dict[str, Any]:
    """FTS5 全文检索 (trigram tokenizer, 中文友好)."""
    cases = CaseRepository.search_fts(q, limit=limit)
    return {
        "query": q,
        "items": [_row_to_case(c) for c in cases],
        "count": len(cases),
    }


@router.get("/pending")
def list_pending_review(
    limit: int = Query(50, ge=1, le=200, description="Page size"),
    offset: int = Query(0, ge=0, description="Pagination offset"),
) -> dict[str, Any]:
    """待人工 review 的案例列表."""
    cases = CaseRepository.list_cases(
        review_status="pending",
        limit=limit,
        offset=offset,
    )
    return {
        "items": [_row_to_case(c) for c in cases],
        "limit": limit,
        "offset": offset,
    }


@router.get("/stats")
def get_stats() -> dict[str, Any]:
    """案例统计数据."""
    return CaseRepository.stats()


@router.get("/by-article/{article_id}")
def get_cases_by_article(article_id: str) -> dict[str, Any]:
    """获取某篇文章的所有案例."""
    conn = get_db()
    art_row = conn.execute("SELECT id FROM articles WHERE id = ?", (article_id,)).fetchone()
    if art_row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    cases = CaseRepository.get_by_article(article_id)
    return {
        "article_id": article_id,
        "items": [_row_to_case(c) for c in cases],
        "count": len(cases),
    }


@router.get("/{case_id}")
def get_case(case_id: str) -> dict[str, Any]:
    """单条案例详情."""
    cs = CaseRepository.get_case(case_id)
    if cs is None:
        raise NotFoundError(message=f"Case {case_id!r} not found")
    return _row_to_case(cs)


@router.post("/retry/{article_id}")
def retry_extraction(
    article_id: str,
    provider: str | None = Query(None, description="LLM provider (default from config)"),
    model: str | None = Query(None, description="LLM model (default from config)"),
) -> dict[str, Any]:
    """手动重试某篇文章的案例提取.

    清除该文章的所有现有案例, 然后同步重新提取.
    注意: 同步调用会阻塞请求, 仅用于手动重试 (单篇文章).
    """
    from moni.cases.pipeline import extract_cases_for_article

    conn = get_db()
    art_row = conn.execute(
        "SELECT id, title, raw_content, source, published_at FROM articles WHERE id = ?",
        (article_id,),
    ).fetchone()
    if art_row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    # 删除已有案例 (重新提取)
    conn.execute("DELETE FROM cases WHERE article_id = ?", (article_id,))
    conn.commit()

    # 同步重新提取
    kwargs = {}
    if provider and model:
        kwargs["provider"] = provider
        kwargs["model"] = model
    result = extract_cases_for_article(dict(art_row), **kwargs)

    return {
        "article_id": article_id,
        "status": result["status"],
        "case_count": result["case_count"],
        "error": result.get("error"),
    }