"""routes/fact_check_plus — v1.0.22 事实核查+ (联网核查) 端点.

3 endpoints, all under /api:

* POST /api/articles/{id}/fact-check-plus              → 202 + job_id + poll_url
* POST /api/fact-check-plus/batch                       → 202 + enqueued / job_ids / skipped / errors
* GET  /api/fact-check-plus/evidence/{article_id}       → 200 + items (按 evidence_seq 排序)
* PATCH /api/fact-check-plus/{article_id}/review        → 200 + state + audit (v1.0.27)

历史背景
----------
v1.0.10 的事实核查是基础档 (LLM 常识推断, 写 ``articles.fact_check_*``,
method='llm_inference')。v1.0.22 升级到联网档:

  - 检索通道: hermes -t web -Q --max-turns 8 (零新依赖, jobs.py 注入)
  - 证据链: 独立表 ``fact_check_evidence`` 落库, 每条结论可按文章/URL/检索关键词反查
  - 双闸防幻觉: 闸 1 证据片段非空 + URL 合法, 闸 2 权威域白名单命中才允许【已核实】
  - 闸 3 时效: days_old 0-30 fresh; 31-90 30d_warn 加后缀; 91+ 90d_demote 强制【存疑】
  - method: 'web_search'

Why this file exists
--------------------
旧 /api/llm/fact-check 同步死代码在 v1.0.22 Day 1 已删除 (moni/web/routes/llm.py:88)。
本文件是 v1.0.22 异步新端点, 复用 articles.py:254 (summary) / articles.py:326 (fact-check) 模式。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from moni.db.connection import get_db
from moni.web.exceptions import (
    BadRequestError,
    NotFoundError,
    ServiceUnavailableError,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["fact-check-plus"])

# ── Domain tiers (服务端判定, 双闸 #2) ────────────────────────────────
# v1.0.100: 白名单搬进 config/fact_check_domains.toml，按三档判定。
# 这里只做兼容再导出，判定逻辑在 moni/llm/domains.py。
# Spec: docs/specs/2026-08-11-fact-check-plus-v1.0.22.md §3.2
from moni.llm.domains import (  # noqa: E402
    DOMAIN_WHITELIST,
    DOMAIN_WHITELIST_REGEX,
    evaluate_authority,
    registrable_domain,
)

MAX_BATCH_SIZE: int = 20

# 闸 3 时效阈值 (v1.0.26) — spec §3.3
TIMELINESS_FRESH_MAX_DAYS: int = 30
TIMELINESS_WARN_MAX_DAYS: int = 90
TIMELINESS_STALE_SUFFIX: str = "(已过期 30-90 天)"


def parse_fetched_at(fetched_at: str | None) -> datetime | None:
    """Parse ISO8601 fetched_at; None / blank / garbage → None (unknown)."""
    if fetched_at is None:
        return None
    text = str(fetched_at).strip()
    if not text:
        return None
    text = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def compute_days_old(
    fetched_at: str | None,
    *,
    now: datetime | None = None,
) -> int | None:
    """距今天数; fetched_at 空/不可解析 → None."""
    dt = parse_fetched_at(fetched_at)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, (now - dt).days)


def compute_timeliness(days_old: int | None) -> str:
    """Map days_old → fresh | 30d_warn | 90d_demote | unknown."""
    if days_old is None:
        return "unknown"
    try:
        d = int(days_old)
    except (TypeError, ValueError):
        return "unknown"
    if d <= TIMELINESS_FRESH_MAX_DAYS:
        return "fresh"
    if d <= TIMELINESS_WARN_MAX_DAYS:
        return "30d_warn"
    return "90d_demote"


def apply_timeliness(conclusion: str, days_old: int | None) -> str:
    """闸 3: 31-90 天加后缀, 91+ 强降级为【存疑】. NULL/≤30 不动."""
    if days_old is None or days_old <= TIMELINESS_FRESH_MAX_DAYS:
        return conclusion
    if days_old <= TIMELINESS_WARN_MAX_DAYS:
        if TIMELINESS_STALE_SUFFIX in (conclusion or ""):
            return conclusion
        return f"{conclusion}{TIMELINESS_STALE_SUFFIX}"
    return "【存疑】"


def is_in_whitelist(domain: str) -> bool:
    """单域是否属于一手来源（tier A）。

    v1.0.100 起不再等同于"过了 Gate 2"——Gate 2 走
    ``evaluate_authority``（≥2 注册域且 ≥1 一手源，或 ≥3 编辑制媒体）。
    """
    from moni.llm.domains import is_in_whitelist as _is_tier_a

    return _is_tier_a(domain)


# ── Pydantic request models ──────────────────────────────────────────────


class _ExtraForbid(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FactCheckPlusRequest(_ExtraForbid):
    """POST /api/articles/{id}/fact-check-plus 请求体.

    provider / model 默认从 llm_settings 读 (active_fact_check_model_id).
    force=true 时覆盖已有联网核查结果 (默认 false).
    """

    provider: str | None = Field(default=None, max_length=100)
    model: str | None = Field(default=None, max_length=100)
    force: bool = False


class BatchRequest(_ExtraForbid):
    """POST /api/fact-check-plus/batch 请求体."""

    article_ids: list[str] = Field(default_factory=list)
    reason: str = Field(default="manual", max_length=20)


class ReviewRequest(_ExtraForbid):
    """PATCH /api/fact-check-plus/{article_id}/review 请求体.

    reviewer 空 → 422 (pydantic min_length); reason 空 → 400 (端点校验).
    """

    state: str = Field(..., max_length=20)
    reviewer: str = Field(..., min_length=1, max_length=100)
    reason: str = Field(default="", max_length=2000)


# ── 状态机 (v1.0.27, spec §3.2 / §4) ────────────────────────────────

FACT_CHECK_STATES: tuple[str, ...] = (
    "draft",
    "verified",
    "published",
    "deprecated",
    "archived",
)

# 相邻档单向; verified 可直达 deprecated/archived; draft 不能跨到 archived
ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    "draft": frozenset({"verified"}),
    "verified": frozenset({"published", "deprecated", "archived"}),
    "published": frozenset({"deprecated", "archived"}),
    "deprecated": frozenset({"archived"}),
    "archived": frozenset(),
}


# ── helpers ──────────────────────────────────────────────────────────────


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _resolve_active_model() -> tuple[str, str]:
    """Read llm_settings.active_fact_check_model_id → (provider, model_id).

    Raises ServiceUnavailableError if not configured.
    """
    conn = get_db()
    active = conn.execute(
        "SELECT value FROM llm_settings WHERE key = 'active_fact_check_model_id'"
    ).fetchone()
    if active is None or not active["value"]:
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
        (active["value"],),
    ).fetchone()
    if model_row is None:
        raise ServiceUnavailableError(
            message=f"Active fact_check model {active['value']!r} not found",
        )
    return model_row["hermes_provider"], model_row["model_id"]


def _has_active_job(article_id: str) -> bool:
    """409 防呆: 同 article 已有 pending/running fact-check-* job → 拒绝."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT COUNT(*) FROM llm_jobs
        WHERE article_id = ?
          AND task_type IN ('fact_check', 'fact_check_plus')
          AND status IN ('pending', 'running')
        """,
        (article_id,),
    ).fetchone()
    return bool(row and row[0])


# ── endpoints ────────────────────────────────────────────────────────────


@router.post("/articles/{article_id}/fact-check-plus", status_code=202)
def fact_check_plus_article(
    article_id: str,
    body: FactCheckPlusRequest,
) -> dict[str, Any]:
    """Enqueue a fact_check_plus job for the given article (v1.0.22).

    Same async shape as ``/summary`` and ``/fact-check`` — returns 202 + job_id;
    the worker writes the conclusion back to ``articles.fact_check_conclusion``
    / ``articles.fact_check_method='web_search'`` and appends evidence rows
    to ``fact_check_evidence``.
    """
    conn = get_db()
    row = conn.execute("SELECT id FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    if _has_active_job(article_id):
        from moni.web.exceptions import ConflictError  # local import keeps top clean

        raise ConflictError(
            message=f"Article {article_id!r} already has active fact_check job",
        )

    if body.provider and body.model:
        provider, model = body.provider, body.model
    else:
        provider, model = _resolve_active_model()

    from moni.llm.jobs import enqueue_job

    job_id = enqueue_job(
        task_type="fact_check_plus",
        article_id=article_id,
        provider=provider,
        model=model,
    )
    return {
        "job_id": job_id,
        "status": "pending",
        "article_id": article_id,
        "task_type": "fact_check_plus",
        "poll_url": f"/api/llm/jobs/{job_id}",
    }


@router.post("/fact-check-plus/batch", status_code=202)
def fact_check_plus_batch(body: BatchRequest) -> dict[str, Any]:
    """Enqueue fact_check_plus jobs for up to 20 articles (v1.0.22).

    Returns per-article status; ``enqueued`` is the count of jobs successfully
    queued, ``skipped`` is the count of articles that already had an active
    job (409 dedup), and ``errors`` is a list of {article_id, reason} for
    articles that failed (404, etc).
    """
    article_ids = body.article_ids or []
    if not article_ids:
        raise BadRequestError(message="article_ids must not be empty")
    if len(article_ids) > MAX_BATCH_SIZE:
        raise BadRequestError(
            message=f"article_ids exceeds batch limit {MAX_BATCH_SIZE}",
            details={"received": len(article_ids), "limit": MAX_BATCH_SIZE},
        )

    provider, model = _resolve_active_model()

    from moni.llm.jobs import enqueue_job

    enqueued: list[str] = []
    skipped: list[str] = []
    errors: list[dict[str, str]] = []
    conn = get_db()
    for aid in article_ids:
        row = conn.execute("SELECT id FROM articles WHERE id = ?", (aid,)).fetchone()
        if row is None:
            errors.append({"article_id": aid, "reason": "not_found"})
            continue
        if _has_active_job(aid):
            skipped.append(aid)
            continue
        try:
            jid = enqueue_job(
                task_type="fact_check_plus",
                article_id=aid,
                provider=provider,
                model=model,
            )
            enqueued.append(jid)
        except Exception as exc:
            logger.warning("fact_check_plus batch enqueue failed for %s: %s", aid, exc)
            errors.append({"article_id": aid, "reason": str(exc)[:100]})

    return {
        "enqueued": len(enqueued),
        "job_ids": enqueued,
        "skipped": skipped,
        "errors": errors,
        "reason": body.reason,
    }


@router.get("/fact-check-plus/evidence/{article_id}")
def fact_check_plus_evidence(article_id: str) -> dict[str, Any]:
    """Return the evidence chain for an article, ordered by evidence_seq.

    Always 200 — ``items=[]`` when the article has no evidence yet (not 404).
    404 only when the article itself does not exist.
    """
    conn = get_db()
    row = conn.execute("SELECT id FROM articles WHERE id = ?", (article_id,)).fetchone()
    if row is None:
        raise NotFoundError(message=f"Article {article_id!r} not found")

    try:
        rows = conn.execute(
            """
            SELECT query, url, domain, snippet, fetched_at, model, provider,
                   conclusion, rationale, evidence_seq, created_at, job_id,
                   days_old
            FROM fact_check_evidence
            WHERE article_id = ?
            ORDER BY evidence_seq ASC
            """,
            (article_id,),
        ).fetchall()
    except Exception:
        # Pre-migration 027: days_old column may be missing.
        rows = conn.execute(
            """
            SELECT query, url, domain, snippet, fetched_at, model, provider,
                   conclusion, rationale, evidence_seq, created_at, job_id
            FROM fact_check_evidence
            WHERE article_id = ?
            ORDER BY evidence_seq ASC
            """,
            (article_id,),
        ).fetchall()
    items: list[dict[str, Any]] = []
    for r in rows:
        item = dict(r)
        days_old = item.get("days_old")
        if days_old is None:
            days_old = compute_days_old(item.get("fetched_at"))
            item["days_old"] = days_old
        item["timeliness"] = compute_timeliness(days_old)
        items.append(item)
    return {"article_id": article_id, "items": items, "total": len(items)}


@router.patch("/fact-check-plus/{article_id}/review")
def fact_check_plus_review(article_id: str, body: ReviewRequest) -> dict[str, Any]:
    """人工覆写状态机: 单向流转 + 写 audit 字段 (v1.0.27).

    body: ``{state, reason, reviewer}``. reason 空 → 400; reviewer 空 → 422.
    跨档 / 回退 → 400; 未知 article → 404.
    """
    if not (body.reason or "").strip():
        raise BadRequestError(message="reason is required")

    target = (body.state or "").strip()
    if target not in FACT_CHECK_STATES:
        raise BadRequestError(
            message=f"state must be one of {FACT_CHECK_STATES}",
            details={"allowed": list(FACT_CHECK_STATES)},
        )

    reviewer = body.reviewer.strip()
    conn = get_db()
    try:
        row = conn.execute(
            """
            SELECT id, COALESCE(fact_check_state, 'draft') AS fact_check_state
            FROM articles WHERE id = ?
            """,
            (article_id,),
        ).fetchone()
    except Exception:
        row = conn.execute(
            "SELECT id FROM articles WHERE id = ?", (article_id,)
        ).fetchone()
        if row is None:
            raise NotFoundError(message=f"Article {article_id!r} not found")
        current = "draft"
    else:
        if row is None:
            raise NotFoundError(message=f"Article {article_id!r} not found")
        current = row["fact_check_state"] or "draft"

    allowed = ALLOWED_TRANSITIONS.get(current, frozenset())
    if target not in allowed:
        raise BadRequestError(
            message=f"Invalid transition {current!r} -> {target!r}",
            details={
                "from": current,
                "to": target,
                "allowed": sorted(allowed),
            },
        )

    now = _now()
    note = body.reason.strip()
    conn.execute(
        """
        UPDATE articles
        SET fact_check_state        = ?,
            fact_check_reviewed_by  = ?,
            fact_check_reviewed_at  = ?,
            fact_check_review_note  = ?
        WHERE id = ?
        """,
        (target, reviewer, now, note, article_id),
    )
    conn.commit()
    return {
        "article_id": article_id,
        "state": target,
        "previous_state": current,
        "reviewed_by": reviewer,
        "reviewed_at": now,
        "review_note": note,
    }
