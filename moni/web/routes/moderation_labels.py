"""routes/moderation_labels — 内容分级标注 (sprint-58).

与 ``routes/moderation.py`` 分开挂载，保证 sprint-54 的
「``/api/moderation`` 只读」不变量不被打破：本模块的写操作只落在
``content_labels`` / ``label_runs`` 两张新表，永不改 ``articles``。
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Body, Query
from fastapi.responses import PlainTextResponse

from moni.moderation import service
from moni.web.exceptions import BadRequestError, NotFoundError

router = APIRouter(prefix="/api/moderation/labels", tags=["moderation-labels"])


@router.get("/summary")
def labels_summary() -> dict[str, Any]:
    """各类计数 + 规则版本 + 最近一次运行。词表只给条数，不展开。"""
    return service.summary()


@router.put("/gate")
def labels_set_gate(enabled: bool = Body(..., embed=True)) -> dict[str, Any]:
    """闸门开关（运行时配置，不需要发版）。"""
    return {"gate_enabled": service.set_gate_enabled(enabled)}


@router.post("/preview")
def labels_preview(
    category: str | None = Query(None),
    limit: int | None = Query(None, ge=1, le=10000),
) -> dict[str, Any]:
    """扫描并写 ``label_runs``，不写任何标签。"""
    return service.preview(category=category, limit=limit)


@router.post("/apply")
def labels_apply(
    category: str | None = Query(None),
    limit: int | None = Query(None, ge=1, le=10000),
) -> dict[str, Any]:
    """写入标签。幂等：第二次 ``new_rows=0``。"""
    return service.apply_labels(category=category, limit=limit)


@router.get("/runs")
def labels_runs(limit: int = Query(20, ge=1, le=100)) -> list[dict[str, Any]]:
    return service.list_runs(limit)


@router.get("/runs/{run_id}/report.md", response_class=PlainTextResponse)
def labels_report(run_id: str) -> PlainTextResponse:
    try:
        run = service.load_run(run_id)
    except KeyError:
        raise NotFoundError(message=f"Label run {run_id!r} not found")
    return PlainTextResponse(
        service.report_markdown(run), media_type="text/markdown; charset=utf-8"
    )


@router.get("/samples")
def labels_samples(
    category: str = Query(..., description="六个类别之一"),
    limit: int = Query(20, ge=1, le=100),
    reveal: bool = Query(False, description="S 类是否返回明文标题"),
) -> list[dict[str, Any]]:
    """抽样：不含正文。S 类默认脱敏，``reveal=1`` 才给明文。"""
    try:
        return service.samples(category, limit=limit, reveal=reveal)
    except ValueError as exc:
        raise BadRequestError(message=str(exc))


@router.post("/{article_id}/{category}/review")
def labels_review(
    article_id: str,
    category: str,
    decision: Literal["confirmed", "dismissed"] = Body(..., embed=True),
    note: str = Body("", embed=True),
) -> dict[str, Any]:
    try:
        return service.review_label(article_id, category, decision, note)
    except KeyError:
        raise NotFoundError(message=f"Label {article_id}/{category} not found")
    except ValueError as exc:
        raise BadRequestError(message=str(exc))
