"""routes/cron — cron job listing, creation and run-history.

Note: the schema only defines ``cron_runs`` (logged executions); there is no
``cron_jobs`` table — job definitions live in ``config/schedule.yaml`` and are
managed by the apscheduler CronScheduler outside the DB.  The API therefore
exposes run history and a best-effort job listing derived from unique ``job_id``
values seen in ``cron_runs``.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.web.models import CronJob, CronJobCreate, CronRun

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/cron", tags=["cron"])


@router.get("/jobs", response_model=list[CronJob])
def list_cron_jobs() -> list[CronJob]:
    """List cron jobs seen in run history (deduplicated by job_id).

    Since job definitions live in ``config/schedule.yaml`` (not the DB), this
    endpoint enumerates all distinct ``job_id`` values from ``cron_runs`` and
    returns a synthetic CronJob object for each.
    """
    conn = get_db()
    rows = conn.execute(
        """
        SELECT DISTINCT job_id,
               MAX(triggered_at) AS last_run_at
        FROM cron_runs
        GROUP BY job_id
        ORDER BY last_run_at DESC
        """,
    ).fetchall()

    def _parse(v: str | None) -> datetime | None:
        if v is None:
            return None
        try:
            return datetime.fromisoformat(v)
        except Exception:
            return None

    jobs: list[CronJob] = []
    for i, row in enumerate(rows, start=1):
        last_run = _parse(row["last_run_at"])
        jobs.append(
            CronJob(
                id=i,
                name=row["job_id"],
                schedule="*",  # schedule unknown — not stored in DB
                source_id=None,
                is_active=True,
                last_run_at=last_run,
                next_run_at=None,
                created_at=last_run,
            )
        )
    return jobs


@router.post("/jobs", response_model=CronJob, status_code=201)
def create_cron_job(body: CronJobCreate) -> CronJob:
    """Create a synthetic cron job entry in run history (no-op on the scheduler).

    v1.0: cron job definitions are managed in ``config/schedule.yaml``, not in
    the DB.  This endpoint persists a dummy row to ``cron_runs`` to record that
    a job definition was requested; a future migration would add a real
    ``cron_jobs`` table.
    """
    conn = get_db()
    now = datetime.now(timezone.utc).isoformat()

    cursor = conn.execute(
        """
        INSERT INTO cron_runs (job_id, triggered_at, finished_at, success, new_articles, sources_run)
        VALUES (?, ?, ?, 1, 0, 0)
        """,
        (body.name, now, now),
    )
    conn.commit()

    return CronJob(
        id=int(cursor.lastrowid),
        name=body.name,
        schedule=body.schedule,
        source_id=body.source_id,
        is_active=body.is_active,
        last_run_at=now,
        next_run_at=None,
        created_at=now,
    )


@router.get("/runs", response_model=list[CronRun])
def list_cron_runs(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
) -> list[CronRun]:
    """List recent cron run records, most recent first, paginated."""
    conn = get_db()
    offset = (page - 1) * page_size

    rows = conn.execute(
        """
        SELECT id, job_id, triggered_at, finished_at, success,
               new_articles, sources_run, error, extra
        FROM cron_runs
        ORDER BY triggered_at DESC
        LIMIT ? OFFSET ?
        """,
        (page_size, offset),
    ).fetchall()

    return [
        CronRun(
            id=row["id"],
            cron_job_id=0,  # legacy: cron_runs uses TEXT job_id, not integer
            started_at=row["triggered_at"],
            finished_at=row["finished_at"],
            status="success" if row["success"] else "failed",
            articles_fetched=row["new_articles"],
            error_message=row["error"],
        )
        for row in rows
    ]
