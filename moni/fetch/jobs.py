"""FetchJobStore — async fetch job tracking (v1.0.7.6).

Used by /api/fetch/sources/{name}/run to return a job_id immediately while
the actual fetch runs in a background thread. The frontend polls
GET /api/fetch/jobs/{job_id} every 5s to render a progress bar.

Lifecycle: pending → running → (done | failed | timeout)

Single source per job. Multi-source orchestration goes through
/api/fetch/all/run which uses cron_runs + fetch_log and is unaffected.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
from typing import Any

from ..db.connection import get_db


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# v1.0.7.6 zombie fix: jobs older than this with status=running are
# considered dead (the daemon thread crashed or the process was killed
# mid-fetch) and should be swept to 'stale' on the next request.
# Must be > JOB_TIMEOUT_SECONDS (10min) so in-thread timeout can finish
# first; 15min catches post-restart zombies without waiting 2h.
STALE_THRESHOLD_SECONDS = 900


def _row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    if d.get("result_json"):
        try:
            d["result"] = json.loads(d["result_json"])
        except (ValueError, TypeError):
            d["result"] = None
        del d["result_json"]
    return d


class FetchJobStore:
    """CRUD for fetch_jobs table. Safe for cross-thread use (WAL + busy_timeout)."""

    # ── create ─────────────────────────────────────────────────────────────

    def create(
        self,
        source: str,
        total_feeds: int = 0,
        batch_id: str | None = None,
    ) -> str:
        """Insert a new job in running state. Returns the job id.

        If ``batch_id`` is provided, this job belongs to a batch (used by
        "全部抓取"). The frontend can poll ``get_batch(batch_id)`` to get
        aggregate progress across all jobs in the batch.
        """
        job_id = str(uuid.uuid4())
        conn = get_db()
        conn.execute(
            """
            INSERT INTO fetch_jobs (
                id, source, status, total_feeds, done_feeds,
                articles_so_far, started_at, batch_id
            ) VALUES (?, ?, 'running', ?, 0, 0, ?, ?)
            """,
            (job_id, source, total_feeds, _now_iso(), batch_id),
        )
        conn.commit()
        return job_id

    # ── progress updates (called from background thread) ───────────────────

    def set_total_feeds(self, job_id: str, total: int) -> None:
        """Update total_feeds (called once at start of fetch)."""
        self._atomic_update(job_id, total_feeds=total)

    def update_progress(
        self,
        job_id: str,
        *,
        done_feeds: int | None = None,
        current_feed: str | None = None,
        articles_so_far: int | None = None,
        total_feeds: int | None = None,
    ) -> None:
        """Atomically bump progress fields. Pass only the fields that changed."""
        fields: dict[str, Any] = {}
        if done_feeds is not None:
            fields["done_feeds"] = done_feeds
        if current_feed is not None:
            fields["current_feed"] = current_feed
        if articles_so_far is not None:
            fields["articles_so_far"] = articles_so_far
        if total_feeds is not None:
            fields["total_feeds"] = total_feeds
        if not fields:
            return
        # Refresh elapsed_seconds on every progress update.
        started_at = self._get_started_at(job_id)
        if started_at:
            try:
                started = datetime.fromisoformat(started_at.replace("Z", "+00:00"))
                fields["elapsed_seconds"] = int((datetime.now(timezone.utc) - started).total_seconds())
            except ValueError:
                pass
        self._atomic_update(job_id, **fields)

    # ── terminal updates ───────────────────────────────────────────────────

    def mark_done(self, job_id: str, result: dict[str, Any]) -> None:
        self._atomic_update(
            job_id,
            status="done",
            finished_at=_now_iso(),
            result_json=json.dumps(result, ensure_ascii=False, default=str),
            current_feed=None,
        )

    def mark_failed(self, job_id: str, error: str) -> None:
        self._atomic_update(
            job_id,
            status="failed",
            finished_at=_now_iso(),
            error=error[:2000],  # cap to keep DB lean
            current_feed=None,
        )

    def mark_timeout(self, job_id: str, error: str = "timeout after 10min") -> None:
        self._atomic_update(
            job_id,
            status="timeout",
            finished_at=_now_iso(),
            error=error[:2000],
            current_feed=None,
        )

    def mark_stale(self, job_id: str) -> None:
        """Mark a long-running job as 'stale' (daemon thread presumed dead).

        Called by sweep_zombies() when a job has been 'running' for more
        than STALE_THRESHOLD_SECONDS. The user-facing API surfaces this
        as an error so the SPA knows to prompt the user to retry.
        """
        self._atomic_update(
            job_id,
            status="stale",
            finished_at=_now_iso(),
            error=(
                "Aborted: daemon thread presumed dead (no progress for "
                f">{STALE_THRESHOLD_SECONDS // 3600}h). "
                "Likely SQLite contention or process restart."
            )[:2000],
            current_feed=None,
        )

    # ── queries (called from request thread) ───────────────────────────────

    def mark_cancelled(self, job_id: str, reason: str = "cancelled by user") -> bool:
        """Soft-cancel a running/pending job so SPA can unblock UI."""
        row = self.get_raw(job_id)
        if not row:
            return False
        if row.get("status") not in ("running", "pending"):
            return False
        self._atomic_update(
            job_id,
            status="cancelled",
            finished_at=_now_iso(),
            error=(reason or "cancelled by user")[:2000],
            current_feed=None,
        )
        return True

    def get_raw(self, job_id: str) -> dict[str, Any] | None:
        """Read job without sweep (cancel path; avoids recursion)."""
        row = get_db().execute(
            "SELECT * FROM fetch_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _row_to_dict(row)

    def _with_live_elapsed(self, d: dict[str, Any] | None) -> dict[str, Any] | None:
        """Fill elapsed_seconds for running jobs on read."""
        if not d or d.get("status") not in ("running", "pending"):
            return d
        started_at = d.get("started_at")
        if not started_at:
            return d
        try:
            started = datetime.fromisoformat(str(started_at).replace("Z", "+00:00"))
            d["elapsed_seconds"] = int((datetime.now(timezone.utc) - started).total_seconds())
        except ValueError:
            pass
        return d

    def get(self, job_id: str) -> dict[str, Any] | None:
        # Poll path: sweep zombies so SPA doesn't pin a dead job forever
        self.sweep_zombies()
        return self._with_live_elapsed(self.get_raw(job_id))

    def list_recent(
        self,
        limit: int = 20,
        status: str | None = None,
        source: str | None = None,
    ) -> list[dict[str, Any]]:
        # Resume-on-load / status=running: clear post-restart zombies first
        self.sweep_zombies()
        # v1.0.7.6-hotfix.1: optional source filter so 409 fallback can scope
        # the running_job_id to the requested source (avoids handing the
        # SPA a job_id from a different source, which would leave the
        # progress modal stuck on "(初始化中)").
        clauses: list[str] = []
        params: list[Any] = []
        if status:
            clauses.append("status = ?")
            params.append(status)
        if source:
            clauses.append("source = ?")
            params.append(source)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        sql = f"SELECT * FROM fetch_jobs{where} ORDER BY started_at DESC LIMIT ?"
        params.append(limit)
        rows = get_db().execute(sql, params).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            d = self._with_live_elapsed(_row_to_dict(r))
            if d is not None:
                result.append(d)
        return result

    # ── batch queries (called from request thread) ────────────────────────

    def get_recent_batch(self) -> str | None:
        """Return the batch_id of the most recent batch (by earliest job
        started_at), or None if no batch jobs exist yet.

        Used by the "📋 上次结果" button to re-open the batch progress
        modal after the user has closed it.
        """
        row = get_db().execute(
            """
            SELECT batch_id, MIN(started_at) AS batch_started
            FROM fetch_jobs
            WHERE batch_id IS NOT NULL
            GROUP BY batch_id
            ORDER BY batch_started DESC
            LIMIT 1
            """
        ).fetchone()
        return row["batch_id"] if row else None

    def get_batch(self, batch_id: str) -> dict[str, Any] | None:
        """Aggregate progress for all jobs sharing a batch_id.

        Returns a dict suitable for the frontend batch progress modal:
        - batch_id, status (running | done | partial | failed)
        - total_sources, done_sources (terminal count)
        - total_feeds, done_feeds (summed across jobs)
        - articles_so_far (summed)
        - current_source, current_feed (from the running job, if any)
        - elapsed_seconds (max across jobs)
        - results: list of per-source result dicts (for done jobs)
        - errors: list of {source, error} for failed jobs
        """
        rows = get_db().execute(
            "SELECT * FROM fetch_jobs WHERE batch_id = ? ORDER BY started_at ASC",
            (batch_id,),
        ).fetchall()
        if not rows:
            return None

        jobs = [_row_to_dict(r) for r in rows]
        jobs = [j for j in jobs if j is not None]

        total_sources = len(jobs)
        done_sources = 0
        failed_sources = 0
        total_feeds = 0
        done_feeds = 0
        articles_so_far = 0
        max_elapsed = 0
        current_source = None
        current_feed = None
        results: list[dict[str, Any]] = []
        errors: list[dict[str, str]] = []

        for j in jobs:
            st = j.get("status", "")
            if st in ("done", "timeout", "stale"):
                done_sources += 1
                if st != "done":
                    failed_sources += 1
            elif st == "failed":
                done_sources += 1
                failed_sources += 1

            total_feeds += j.get("total_feeds", 0) or 0
            done_feeds += j.get("done_feeds", 0) or 0
            articles_so_far += j.get("articles_so_far", 0) or 0
            elapsed = j.get("elapsed_seconds", 0) or 0
            if elapsed > max_elapsed:
                max_elapsed = elapsed

            if st == "running":
                cf = j.get("current_feed")
                # Skip pending jobs (pre-created with '(排队中)' marker)
                if cf and cf != "(排队中)":
                    current_source = j.get("source")
                    current_feed = cf

            if st == "done" and j.get("result"):
                results.append(j["result"])
            elif st in ("failed", "timeout", "stale") and j.get("error"):
                errors.append({"source": j.get("source", "?"), "error": j["error"]})

        # Determine batch status
        if done_sources < total_sources:
            status = "running"
        elif failed_sources == 0:
            status = "done"
        elif failed_sources == total_sources:
            status = "failed"
        else:
            status = "partial"

        return {
            "batch_id": batch_id,
            "status": status,
            "total_sources": total_sources,
            "done_sources": done_sources,
            "failed_sources": failed_sources,
            "total_feeds": total_feeds,
            "done_feeds": done_feeds,
            "articles_so_far": articles_so_far,
            "current_source": current_source,
            "current_feed": current_feed,
            "elapsed_seconds": max_elapsed,
            "results": results,
            "errors": errors,
        }

    def has_running(self, source: str | None = None) -> bool:
        """True if any job is still running (optionally for a specific source).

        Used by /api/fetch/sources/{name}/run to refuse concurrent fetches
        of the same source. Sweeps stale jobs first so a dead daemon
        doesn't block new fetches forever.
        """
        self.sweep_zombies()
        if source:
            row = get_db().execute(
                "SELECT 1 FROM fetch_jobs WHERE status = 'running' AND source = ? LIMIT 1",
                (source,),
            ).fetchone()
        else:
            row = get_db().execute(
                "SELECT 1 FROM fetch_jobs WHERE status = 'running' LIMIT 1"
            ).fetchone()
        return row is not None

    def sweep_zombies(self) -> int:
        """Mark any 'running' job older than STALE_THRESHOLD_SECONDS as 'stale'.

        Returns the number of jobs swept. Safe to call on every
        /api/fetch/sources/{name}/run request — idempotent and cheap
        (a single UPDATE statement).
        """
        from datetime import timedelta
        cutoff = (datetime.now(timezone.utc) - timedelta(seconds=STALE_THRESHOLD_SECONDS)).isoformat()
        cur = get_db().execute(
            "SELECT id FROM fetch_jobs WHERE status = 'running' AND started_at < ?",
            (cutoff,),
        )
        stale_ids = [row["id"] for row in cur.fetchall()]
        for jid in stale_ids:
            self.mark_stale(jid)
        return len(stale_ids)

    # ── internals ──────────────────────────────────────────────────────────

    def _atomic_update(self, job_id: str, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [job_id]
        get_db().execute(
            f"UPDATE fetch_jobs SET {cols} WHERE id = ?", params
        )
        get_db().commit()

    def _get_started_at(self, job_id: str) -> str | None:
        row = get_db().execute(
            "SELECT started_at FROM fetch_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return row["started_at"] if row else None
