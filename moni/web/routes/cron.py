"""routes/cron — cron job registry, run history, and scheduler status.

Job definitions live in ``cron_jobs`` (migration 056, seeded from
``config/schedule.yaml``).  The scheduler process re-reads them when
``cron_meta.schedule_revision`` changes, so edits here take effect within
one 15-second tick without restarting the process.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Query

from moni.cron import registry
from moni.db.connection import get_db
from moni.web.models import CronJob, CronJobUpdate, CronRun

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cron", tags=["cron"])


def _last_runs(conn) -> dict[str, str]:
    rows = conn.execute(
        "SELECT job_id, MAX(triggered_at) AS last_run_at FROM cron_runs GROUP BY job_id"
    ).fetchall()
    return {row["job_id"]: row["last_run_at"] for row in rows if row["job_id"]}


def _to_model(job: dict, last_runs: dict[str, str]) -> CronJob:
    next_run = registry.next_run_at(job["hour"], job["minute"], job["day_of_week"])
    return CronJob(
        id=job["id"],
        name=job["name"],
        func_path=job["func_path"],
        trigger_type=job["trigger_type"],
        schedule={
            "hour": job["hour"],
            "minute": job["minute"],
            "day_of_week": job["day_of_week"],
        },
        func_kwargs=job["func_kwargs"],
        is_active=job["is_active"],
        last_run_at=last_runs.get(job["id"]),
        next_run_at=next_run.isoformat() if next_run else None,
        created_at=job.get("created_at"),
        updated_at=job.get("updated_at"),
    )


@router.get("/jobs", response_model=list[CronJob])
def list_cron_jobs() -> list[CronJob]:
    """List task definitions from the registry with last/next run times."""
    conn = get_db()
    last_runs = _last_runs(conn)
    return [_to_model(job, last_runs) for job in registry.list_jobs(conn)]


@router.put("/jobs/{job_id}", response_model=CronJob)
def update_cron_job(job_id: str, body: CronJobUpdate) -> CronJob:
    """Update schedule / kwargs / enabled flag and bump the schedule revision."""
    conn = get_db()
    patch = body.model_dump(exclude_none=True)
    try:
        registry.update_job(conn, job_id, patch)
    except registry.CronRegistryError as exc:
        if registry.get_job(conn, job_id) is None:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    job = registry.get_job(conn, job_id)
    assert job is not None
    return _to_model(job, _last_runs(conn))


@router.post("/jobs/{job_id}/run", status_code=202)
def run_cron_job(job_id: str) -> dict:
    """Queue an immediate run; the scheduler executes it on its next tick."""
    conn = get_db()
    try:
        request_id = registry.enqueue_run(conn, job_id)
    except registry.AlreadyQueued as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except registry.CronRegistryError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"request_id": request_id, "job_id": job_id, "status": "queued"}


@router.post("/jobs", status_code=410)
def create_cron_job() -> None:
    """Removed: job definitions are code-bound and seeded from YAML."""
    raise HTTPException(
        status_code=410,
        detail="定时任务由系统预置，不支持通过接口新建；请使用 PUT /api/cron/jobs/{id} 修改。",
    )


@router.get("/status")
def cron_status() -> dict:
    """Scheduler liveness derived from the heartbeat written every 15s."""
    conn = get_db()
    age = registry.heartbeat_age_sec(conn)
    jobs = registry.list_jobs(conn)
    return {
        "running": age is not None and age <= 60,
        "heartbeat_age_sec": age,
        "job_count": len(jobs),
        "active_job_count": sum(1 for job in jobs if job["is_active"]),
        "revision": registry.get_revision(conn),
    }


def _duration_seconds(started: str | None, finished: str | None) -> float | None:
    """Best-effort duration in seconds from two ISO timestamps."""
    if not started or not finished:
        return None
    try:
        s = datetime.fromisoformat(str(started).replace("Z", "+00:00"))
        f = datetime.fromisoformat(str(finished).replace("Z", "+00:00"))
        return round((f - s).total_seconds(), 1)
    except Exception:
        return None


@router.get("/runs", response_model=list[CronRun])
def list_cron_runs(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
    job_id: str | None = Query(None, description="Filter by job id"),
) -> list[CronRun]:
    """List recent cron runs (newest first), optionally filtered by job id."""
    conn = get_db()
    offset = (page - 1) * page_size

    where = "WHERE job_id = ?" if job_id else ""
    params: list = [job_id] if job_id else []
    rows = conn.execute(
        f"""
        SELECT id, job_id, triggered_at, finished_at, success,
               new_articles, sources_run, error, extra
        FROM cron_runs
        {where}
        ORDER BY triggered_at DESC
        LIMIT ? OFFSET ?
        """,
        (*params, page_size, offset),
    ).fetchall()

    out: list[CronRun] = []
    for row in rows:
        ok = bool(row["success"])
        triggered = row["triggered_at"]
        finished = row["finished_at"]
        n = int(row["new_articles"] or 0)
        err = row["error"]
        extra = None
        if row["extra"]:
            try:
                parsed = json.loads(row["extra"])
                extra = parsed if isinstance(parsed, dict) else None
            except (TypeError, ValueError):
                extra = None
        out.append(
            CronRun(
                id=row["id"],
                job_id=row["job_id"] or "",
                cron_job_id=0,
                triggered_at=triggered,
                started_at=triggered,
                finished_at=finished,
                status="success" if ok else "failed",
                success=ok,
                new_articles=n,
                articles_fetched=n,
                duration=_duration_seconds(triggered, finished),
                log_url=None,
                error_message=err,
                error=err,
                extra=extra,
            )
        )
    return out
