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
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from moni.llm.jobs import get_job, list_jobs

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/llm/jobs", tags=["llm-jobs"])


@router.get("")
def list_llm_jobs(
    status: str | None = Query(default=None, pattern="^(pending|running|done|failed|cancelled)$"),
    article_id: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
) -> list[dict[str, Any]]:
    """List LLM jobs newest first, optional status/article filters."""
    return list_jobs(status=status, article_id=article_id, limit=limit)


@router.get("/stats/summary")
def jobs_stats_summary() -> dict[str, int]:
    """Return ``{status: count}`` for the jobs panel header chip.

    Bypasses ``moni.llm.jobs.list_jobs`` because we want a single
    aggregate query — fetching 500 rows just to count them is wasteful.
    """
    from moni.db.connection import get_db

    conn = get_db()
    rows = conn.execute(
        "SELECT status, COUNT(*) AS n FROM llm_jobs GROUP BY status"
    ).fetchall()
    out: dict[str, int] = {
        "pending": 0, "running": 0, "done": 0, "failed": 0, "cancelled": 0,
    }
    for r in rows:
        out[r["status"]] = int(r["n"])
    return out


@router.get("/{job_id}")
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
    return job


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
