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
from pydantic import BaseModel

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


@router.post("/{case_id}/confirm")
def confirm_case(case_id: str) -> dict[str, Any]:
    """人工核验案例 (review_status pending → confirmed + lifecycle draft → verified).

    v1.0.35.19: 激活此前死代码 CaseRepository.confirm/update_lifecycle,
    照 events confirm 范式补单条闭环 (grok F1: 案例库无 review 闭环)。
    申报"已核验案例"口径 = 本端点确认过的案例。
    """
    conn = get_db()
    with conn:
        ok = CaseRepository.confirm(conn, case_id)
        if ok:
            CaseRepository.update_lifecycle(conn, case_id, "verified")
    if not ok:
        raise NotFoundError(
            message=f"Case {case_id!r} not found or already reviewed"
        )
    return {
        "case_id": case_id,
        "review_status": "confirmed",
        "case_lifecycle": "verified",
    }


@router.post("/{case_id}/reject")
def reject_case(
    case_id: str,
    reason: str | None = Query(None, description="驳回原因 (可选)"),
) -> dict[str, Any]:
    """人工驳回案例 (review_status pending → rejected).

    v1.0.35.19: 照 events reject 范式。
    """
    conn = get_db()
    ok = CaseRepository.reject(conn, case_id, reason)
    conn.commit()
    if not ok:
        raise NotFoundError(
            message=f"Case {case_id!r} not found or already reviewed"
        )
    return {"case_id": case_id, "review_status": "rejected"}


# ── 编辑 / 软删除 / 关键词 (v1.0.35.19 agy v3 评审) ──────────────────

class CaseUpdateBody(BaseModel):
    """案例编辑请求体 (字段白名单在 repository 层校验)."""

    subject: str | None = None
    subject_raw: str | None = None
    case_type: str | None = None
    case_action: str | None = None
    counterparty: str | None = None
    amount: str | None = None
    reference_value: str | None = None
    reference_rationale: str | None = None
    summary: str | None = None
    evidence_snippet: str | None = None
    case_date: str | None = None
    case_date_precision: str | None = None


@router.patch("/{case_id}")
def update_case(case_id: str, body: CaseUpdateBody) -> dict[str, Any]:
    """编辑案例字段 (v1.0.35.19 agy v3 需求1)."""
    updates = {k: v for k, v in body.model_dump().items() if v is not None}
    ok = CaseRepository.update_case(case_id, updates)
    if not ok:
        raise NotFoundError(message=f"Case {case_id!r} not found or no editable field")
    return {"case_id": case_id, "updated": sorted(updates.keys())}


@router.delete("/{case_id}")
def delete_case(case_id: str) -> dict[str, Any]:
    """软删除案例 (lifecycle → archived, 可恢复)."""
    ok = CaseRepository.soft_delete_case(case_id)
    if not ok:
        raise NotFoundError(message=f"Case {case_id!r} not found or already archived")
    return {"case_id": case_id, "case_lifecycle": "archived"}


@router.get("/{case_id}/keywords")
def list_case_keywords(case_id: str) -> dict[str, Any]:
    """案例关键词列表 (v1.0.35.19 agy v3 需求3)."""
    if CaseRepository.get_case(case_id) is None:
        raise NotFoundError(message=f"Case {case_id!r} not found")
    return {"case_id": case_id, "items": CaseRepository.list_keywords(case_id)}


class KeywordBody(BaseModel):
    keyword: str
    weight: int = 1


@router.post("/{case_id}/keywords")
def add_case_keyword(case_id: str, body: KeywordBody) -> dict[str, Any]:
    """添加人工关键词 (manual_user)."""
    if CaseRepository.get_case(case_id) is None:
        raise NotFoundError(message=f"Case {case_id!r} not found")
    new_id = CaseRepository.add_keyword(case_id, body.keyword, weight=body.weight)
    return {"case_id": case_id, "keyword": body.keyword.strip(), "id": new_id}


@router.delete("/{case_id}/keywords/{keyword}")
def remove_case_keyword(case_id: str, keyword: str) -> dict[str, Any]:
    """删除人工关键词 (manual_user 才可删)."""
    ok = CaseRepository.remove_keyword(case_id, keyword)
    if not ok:
        raise NotFoundError(
            message=f"Keyword {keyword!r} not found on {case_id!r} or not manual_user"
        )
    return {"case_id": case_id, "keyword": keyword, "deleted": True}