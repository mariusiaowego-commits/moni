"""脱敏实验室 API（sprint-69）—— 决赛演示用的四栏演练台。

三个端点，全部**新增**，不改既有 API 契约：

- ``POST /api/desensitize/preview``  纯本地遮蔽预览（不发网络、不写库）
- ``POST /api/desensitize/run``      遮蔽 → 真调大模型 → 取回原始回包 → 本地还原
- ``GET  /api/desensitize/stats``    审计账本汇总（生产口径排除实验室自己的调用）

``run`` 走的是生产同一条通道（``FailoverRunner`` → ``hermes``），区别只在于
调用方已经完成遮蔽，所以 runner 既不二次遮蔽也不还原 —— 这样第三栏拿到的
是模型**原始**回包（只有编码），第四栏由实验室自己还原。
"""

from __future__ import annotations

import logging
import sqlite3
from typing import Any

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field, field_validator

from moni.db.connection import get_db
from moni.llm.client import _get_runner
from moni.llm.desensitize.audit import (
    OutboundContext,
    finalize_inbound,
    load_settings,
    prepare_outbound,
    record_event,
)
from moni.llm.runner import CallRequest
from moni.web.exceptions import BadRequestError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/desensitize", tags=["desensitize"])

#: 实验室调用在账本里的 call_site —— 生产口径统计时排除。
LAB_CALL_SITE = "desens_lab"

#: 允许在实验室里试的任务类型（决定策略：standard / 联网核查保留公开主体）
_ALLOWED_TASK_TYPES = ("standard", "fact_check_plus")

_MAX_TEXT_CHARS = 8000
_MAX_TERM_CHARS = 64


class LabTextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=_MAX_TEXT_CHARS)
    task_type: str = "standard"
    #: 临时敏感词（等价于用户规则里的机构名条目）—— 只在本次调用生效，不写库。
    extra_terms: list[str] = Field(default_factory=list, max_length=32)

    @field_validator("extra_terms")
    @classmethod
    def _check_term_length(cls, terms: list[str]) -> list[str]:
        for term in terms:
            if len(term.strip()) > _MAX_TERM_CHARS:
                raise ValueError(f"临时敏感词超过 {_MAX_TERM_CHARS} 字符上限")
        return terms


class LabRunRequest(LabTextRequest):
    timeout: int = Field(default=120, ge=5, le=600)


def _ctx_for(req: LabTextRequest) -> OutboundContext:
    task_type = req.task_type if req.task_type in _ALLOWED_TASK_TYPES else "standard"
    try:
        ctx = prepare_outbound(
            req.text,
            call_site=LAB_CALL_SITE,
            task_type=task_type,
            force=True,
            inject_note=True,
            extra_terms=req.extra_terms,
        )
    except Exception as exc:
        # force=True 是 fail-closed：遮蔽器异常时内核直接抛错，文本绝不会出网。
        raise BadRequestError(message=f"脱敏内核异常，已 fail-closed 拦截（未出网）：{exc}") from exc
    if ctx is None:  # pragma: no cover — force=True always returns a context
        raise BadRequestError(message="脱敏内核未返回上下文")
    return ctx


def _items(ctx: OutboundContext) -> list[dict[str, Any]]:
    """遮蔽明细 —— 只有占位符与类型，**不含原值**（与账本同一口径）。"""
    return [
        {
            "kind": item.kind,
            "placeholder": item.placeholder,
            "by": item.by,
            "length": item.length,
            "action": item.action,
        }
        for item in ctx.mask_result.items
    ]


@router.post("/preview")
def post_preview(req: LabTextRequest = Body(...)) -> dict[str, Any]:
    """纯本地遮蔽预览：不出网、不写库、不调用大模型。"""
    ctx = _ctx_for(req)
    return {
        "task_type": ctx.task_type,
        "policy_mode": ctx.policy_mode,
        "blocked": ctx.mask_result.blocked,
        "blocked_reason": ctx.mask_result.blocked_reason,
        "masked_text": ctx.masked_text,
        "masked_total": ctx.mask_result.masked_total,
        "masked_distinct": ctx.mask_result.masked_distinct,
        "kinds": dict(ctx.mask_result.kinds),
        "items": _items(ctx),
        "latency_us": ctx.mask_result.latency_us,
    }


@router.post("/run")
def post_run(req: LabRunRequest = Body(...)) -> dict[str, Any]:
    """四栏实跑：遮蔽 → 真调大模型 → 原始回包 → 本地还原。"""
    ctx = _ctx_for(req)
    if ctx.mask_result.blocked:
        # 内核判定「这单遮不干净」（规则 action=block 或出站泄漏扫描命中）→ 不出网。
        record_event(ctx, blocked=True)
        raise BadRequestError(
            message=f"已拦截，未出网：{ctx.mask_result.blocked_reason or 'blocked'}"
        )
    runner = _get_runner()
    try:
        result = runner.call(
            CallRequest(
                provider="",
                model_id="",
                prompt=ctx.masked_text,
                timeout=req.timeout,
                task_type=ctx.task_type,
                desens_ctx=ctx,
            )
        )
    except Exception as exc:
        logger.warning("desensitize lab run failed: %s", exc)
        # 出网过了但没拿到回包：这单也要进账本，否则「调用次数」会系统性少记。
        ctx.fallback_override = "model_error"
        record_event(ctx, blocked=False)
        raise BadRequestError(message=f"大模型调用失败：{exc}") from exc

    reply_raw = result.reply_text
    # 入站还原走 fail-open：还原失败就把模型原包原样展示并记 restore_error，
    # 不能因为还原器出错把这一单从账本里抹掉（出站仍是 fail-closed）。
    ctx.strict_fail_closed = False
    restored = finalize_inbound(ctx, reply_raw)
    record_event(ctx, provider=result.provider, model=result.model_id)
    stats = ctx.vault.stats
    return {
        "task_type": ctx.task_type,
        "policy_mode": ctx.policy_mode,
        "original": req.text,
        "masked": ctx.masked_text,
        "reply_raw": reply_raw,
        "restored": restored,
        "fallback_used": ctx.fallback_override or ctx.mask_result.fallback_used,
        "items": _items(ctx),
        "stats": {
            "masked_total": ctx.mask_result.masked_total,
            "masked_distinct": ctx.mask_result.masked_distinct,
            "restored_total": stats.restored_total,
            "fuzzy_restored_total": stats.fuzzy_restored_total,
            "unrestored_count": stats.unrestored_count,
            "hallucinated_count": stats.hallucinated_count,
            "residual_placeholders": stats.residual_placeholders,
            "leak_scan_hits": ctx.mask_result.leak_scan_hits,
            "mask_latency_us": ctx.mask_result.latency_us,
            "restore_latency_us": stats.restore_latency_us,
            "model_duration_ms": result.duration_ms,
        },
    }


@router.get("/stats")
def get_stats(limit: int = 20) -> dict[str, Any]:
    """审计账本汇总：生产口径（排除实验室调用）+ 实验室自己的最近若干条。"""
    conn = get_db()
    totals = _aggregate(conn, exclude_lab=True)
    lab = _aggregate(conn, exclude_lab=False)
    recent = _recent_rows(conn, limit)
    settings = load_settings()
    return {
        "enabled": settings.enabled,
        "levels": {
            "level_l1": settings.level_l1,
            "level_l2": settings.level_l2,
            "level_l3": settings.level_l3,
            "strict_fail_closed": settings.strict_fail_closed,
        },
        "totals": totals,
        "lab_totals": lab,
        "recent": recent,
    }


def _aggregate(conn: sqlite3.Connection, *, exclude_lab: bool) -> dict[str, Any]:
    where = "WHERE call_site != ?" if exclude_lab else ""
    params: tuple[Any, ...] = (LAB_CALL_SITE,) if exclude_lab else ()
    row = conn.execute(
        f"""
        SELECT count(*) AS calls,
               sum(blocked) AS blocked_calls,
               sum(masked_total) AS masked_total,
               sum(restored_total) AS restored_total,
               sum(unrestored_count) AS unrestored_count,
               sum(leak_scan_hits) AS leak_scan_hits,
               sum(residual_placeholders) AS residual_placeholders
        FROM desensitize_events {where}
        """,
        params,
    ).fetchone()
    calls = int(row["calls"] or 0)
    blocked_calls = int(row["blocked_calls"] or 0)
    restored = int(row["restored_total"] or 0)
    unrestored = int(row["unrestored_count"] or 0)
    samples = restored + unrestored
    return {
        "calls": calls,
        "blocked_calls": blocked_calls,
        "outbound_calls": calls - blocked_calls,
        "masked_total": int(row["masked_total"] or 0),
        "restored_total": restored,
        "unrestored_count": unrestored,
        "leak_scan_hits": int(row["leak_scan_hits"] or 0),
        "residual_placeholders": int(row["residual_placeholders"] or 0),
        # 没有样本时给 None（页面显示 —），不用 1.0 冒充 100% 成功率。
        "restore_success_rate": (restored / samples) if samples else None,
    }


def _recent_rows(conn: sqlite3.Connection, limit: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, call_site, task_type, provider, model, policy_mode,
               masked_total, restored_total, unrestored_count,
               leak_scan_hits, residual_placeholders, blocked, fallback_used, created_at
        FROM desensitize_events
        ORDER BY id DESC
        LIMIT ?
        """,
        (max(1, min(limit, 100)),),
    ).fetchall()
    return [dict(row) for row in rows]
