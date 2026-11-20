"""routes/llm_jobs — list / poll / cancel endpoints for ``llm_jobs`` rows.

Companion to ``moni/llm/jobs.py``.  The worker writes job status into
the ``llm_jobs`` table; this module exposes the read side to the SPA
(the article "查看" modal and the new "LLM 任务" menu) and a small
``POST /jobs/{id}/cancel`` for users who don't want to wait.

Routes (all under ``/api/llm/jobs``):

* ``GET  /``            — list jobs (newest first), optional ``?status=``
                          and ``?article_id=`` filters
* ``GET  /{job_id}``    — single job (used by the SPA for polling)
* ``POST /{job_id}/cancel`` — flip a pending job to ``cancelled``
                          (worker drops cancelled rows on the way past)
* ``GET  /stats/summary`` — counts of jobs grouped by status; the
                          LLM-jobs panel uses this for the header chip
* ``GET  /stats/duration`` — p50 / p90 wall clock of recent finished jobs;
                          the reader shows "这类任务通常 X 分钟" instead of
                          guessing

Contract note: every route here is declared with a ``response_model``, so
``/openapi.json`` is the source of truth for the field names/types the SPA
reads (``tests/test_llm_jobs_contract.py`` diffs it).
"""

from __future__ import annotations

import logging
import math
from datetime import datetime, timezone
from typing import Any, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, ConfigDict

from moni.llm.jobs import get_job, list_jobs
from moni.llm.phases import phase_of

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm/jobs", tags=["llm-jobs"])

#: 允许出现在 ``?status=`` 里的全部取值（含 leases 判死的 ``stale``）。
_STATUS_PATTERN = "^(pending|running|done|failed|stale|cancelled)$"


# ── 契约模型 ───────────────────────────────────────────────────────────────
#
# 挂了 ``response_model`` 之后 /openapi.json 就是闸门：改字段名/类型而不改
# 前端，contract 测试会红。


class JobProgress(BaseModel):
    """流式进度快照（PR-4 起由 ``moni/llm/progress.py`` 填）。

    PR-3 阶段恒为 ``None``：进度还没接线，前端只显示秒表。
    """

    stage: Literal["thinking", "searching", "writing"]
    searches: int = 0
    last_query: str = ""
    last_tool_ms: int | None = None
    last_tool_error: bool = False
    last_event_at: str | None = None


class JobOut(BaseModel):
    """``llm_jobs`` 一行 + 派生字段（phase / 排队位次 / fact_check）。

    ``extra="allow"`` 是故意的：pydantic 默认把未声明的字段**静默剥离**，
    而这张表还有 result / fence / conversation_id 等列有人读。下面显式列出
    的都是前端已经在读的字段 —— 列出来是为了让 openapi 校验类型，也顺带
    防止改模型时把它们吃掉。
    """

    model_config = ConfigDict(extra="allow")

    id: str
    task_type: str
    article_id: str
    status: str
    phase: str
    created_at: str
    started_at: str | None = None
    finished_at: str | None = None
    duration_ms: int | None = None
    result: str | None = None
    error: str | None = None
    provider: str | None = None
    model: str | None = None
    attempts: int = 0
    article_title: str | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    #: 仅 pending 有值：1 基 FIFO 位次 / 当下排队总数（含正在跑的那条）。
    queue_position: int | None = None
    queue_total: int | None = None
    progress: JobProgress | None = None
    #: 仅 fact_check_plus 且 done 时有值（人话结论 + 证据链）。
    fact_check: dict[str, Any] | None = None


class DurationStats(BaseModel):
    """同类任务最近的耗时基线；样本不足 3 条时 p50/p90 回 ``None``。"""

    n: int
    p50_sec: float | None = None
    p90_sec: float | None = None


class JobsStats(BaseModel):
    pending: int = 0
    running: int = 0
    done: int = 0
    failed: int = 0
    stale: int = 0
    cancelled: int = 0


def _percentile(sorted_values: list[int], pct: int) -> float:
    """nearest-rank 百分位（样本少时够用，不做插值）。"""
    rank = math.ceil(pct / 100 * len(sorted_values))
    return float(sorted_values[max(rank - 1, 0)])


def _annotate(jobs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """补 phase 与 pending 的排队位次（整表一次算完，避免 N+1）。"""
    from moni.db.connection import get_db

    conn = get_db()
    running = int(
        conn.execute(
            "SELECT COUNT(*) AS n FROM llm_jobs WHERE status = 'running'"
        ).fetchone()["n"]
    )
    pending_ids = [
        str(r["id"])
        for r in conn.execute(
            "SELECT id FROM llm_jobs WHERE status = 'pending' ORDER BY created_at ASC, id ASC"
        )
    ]
    total = running + len(pending_ids)
    position = {job_id: running + 1 + i for i, job_id in enumerate(pending_ids)}
    for job in jobs:
        job["phase"] = phase_of(job.get("status"))
        if job.get("status") == "pending":
            job["queue_position"] = position.get(str(job["id"]))
            job["queue_total"] = total
    return jobs


@router.get("", response_model=list[JobOut])
def list_llm_jobs(
    status: str | None = Query(default=None, pattern=_STATUS_PATTERN),
    article_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """List LLM jobs newest first, optional status/article filters."""
    return _annotate(list_jobs(status=status, article_id=article_id, limit=limit))


@router.get("/stats/summary", response_model=JobsStats)
def jobs_stats_summary() -> JobsStats:
    """Return ``{status: count}`` for the jobs panel header chip.

    Bypasses ``moni.llm.jobs.list_jobs`` because we want a single
    aggregate query — fetching 500 rows just to count them is wasteful.
    """
    from moni.db.connection import get_db

    conn = get_db()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM llm_jobs GROUP BY status"
    ).fetchall()
    out = JobsStats()
    for r in rows:
        key = str(r["status"])
        if key in JobsStats.model_fields:
            setattr(out, key, int(r["n"]))
    return out


@router.get("/stats/duration", response_model=DurationStats)
def jobs_duration_stats(
    task_type: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=500),
) -> DurationStats:
    """最近 N 条**成功**同型任务的耗时基线（p50 / p90，单位秒）。

    口径固定 ``status='done' AND duration_ms > 0``：失败/取消的行会把「这类
    任务通常要多久」带偏，``duration_ms`` 为空的旧行同理。样本不足 3 条时
    只回 ``n``、p50/p90 为 ``None`` —— 前端据此不显示基线，而不是拿 1 个
    样本算出一个「通常耗时」。

    路由必须注册在 ``/{job_id}`` **之前**，否则 ``/stats/duration`` 会被
    当成 job_id 吃掉。
    """
    from moni.db.connection import get_db

    conn = get_db()
    sql = "SELECT duration_ms FROM llm_jobs WHERE status = 'done' AND duration_ms > 0"
    params: list[Any] = []
    if task_type:
        sql += " AND task_type = ?"
        params.append(task_type)
    sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
    params.append(limit)
    values = sorted(int(r["duration_ms"]) for r in conn.execute(sql, params))
    if len(values) < 3:
        return DurationStats(n=len(values))
    return DurationStats(
        n=len(values),
        p50_sec=round(_percentile(values, 50) / 1000, 1),
        p90_sec=round(_percentile(values, 90) / 1000, 1),
    )


@router.get("/{job_id}", response_model=JobOut)
def get_llm_job(job_id: str) -> dict[str, Any]:
    """Return one job row (the SPA polls this every 1s).

    v1.0.35.13 (follow-up): 若 job 是 ``fact_check_plus`` 且 done, 附加
    ``fact_check`` 结构化字段 {conclusion, evidence: [...]} —— 来自 articles
    + fact_check_evidence 表 (用户友好渲染用), 而非 LLM 原始 JSON。这样
    /llmJobs 详情 modal 能展示可读结论与证据链接, 不甩原始 JSON 给用户。
    """
    from moni.db.connection import get_db
    from moni.web.exceptions import NotFoundError

    job = get_job(job_id)
    if job is None:
        raise NotFoundError(message=f"LLM job {job_id!r} not found")

    # 只对联网核查的已完成任务附加友好结构化数据
    if (
        job.get("task_type") == "fact_check_plus"
        and job.get("status") == "done"
        and job.get("article_id")
    ):
        conn = get_db()
        art = conn.execute(
            "SELECT fact_check_conclusion, fact_check_note "
            "FROM articles WHERE id = ?",
            (job["article_id"],),
        ).fetchone()
        conclusion = (dict(art).get("fact_check_conclusion") if art else None) or None

        # evidence rows (evidence_seq 升序)
        ev_rows = conn.execute(
            """
            SELECT query, url, domain, snippet, rationale, fetched_at,
                   model, provider, conclusion, evidence_seq
            FROM fact_check_evidence
            WHERE article_id = ?
            ORDER BY evidence_seq ASC
            """,
            (job["article_id"],),
        ).fetchall()
        job["fact_check"] = {
            "conclusion": conclusion,
            "evidence": [dict(r) for r in ev_rows],
        }
    return _annotate([job])[0]


@router.post("/{job_id}/cancel")
def cancel_llm_job(job_id: str) -> dict[str, Any]:
    """Mark a pending or running job as cancelled.

    Behaviour:
      * ``pending``  -> ``cancelled`` (worker skips it)
      * ``running``  -> ``cancelled`` (the in-flight LLM call continues
                       to completion; the worker just discards the
                       result when it sees the row is no longer ours.
                       This is the best we can do without killing the
                       subprocess from a different thread, which would
                       race with SQLite writes.)
      * ``done`` / ``failed`` / ``cancelled`` -> 409 Conflict; nothing
                       to do.

    Returns the updated job row.
    """
    from moni.db.connection import get_db
    from moni.web.exceptions import ConflictError, NotFoundError

    conn = get_db()
    row = conn.execute(
        "SELECT id, status, task_type FROM llm_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if row is None:
        raise NotFoundError(message=f"LLM job {job_id!r} not found")
    if row["status"] in ("done", "failed", "cancelled"):
        raise ConflictError(
            message=f"Job {job_id!r} is already {row['status']!r}; cannot cancel",
        )
    if row["task_type"] == "reader_chat":
        from moni.llm.reader_worker import cancel_reader_run

        cancel_reader_run(job_id)
    else:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "UPDATE llm_jobs SET status = 'cancelled', finished_at = ? WHERE id = ?",
            (now, job_id),
        )
        conn.commit()
    logger.info("[llm_jobs] job %s cancelled by user", job_id)
    job = get_job(job_id)
    assert job is not None
    return job
