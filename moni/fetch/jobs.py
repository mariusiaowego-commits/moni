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
# 2h is well above JOB_TIMEOUT_SECONDS (10min) so legitimate long fetches
# are never incorrectly swept.
STALE_THRESHOLD_SECONDS = 7200


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

    def create(self, source: str, total_feeds: int = 0) -> str:
        """Insert a new job in running state. Returns the job id."""
        job_id = str(uuid.uuid4())
        conn = get_db()
        conn.execute(
            """
            INSERT INTO fetch_jobs (
                id, source, status, total_feeds, done_feeds,
                articles_so_far, started_at
            ) VALUES (?, ?, 'running', ?, 0, 0, ?)
            """,
            (job_id, source, total_feeds, _now_iso()),
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

    def get(self, job_id: str) -> dict[str, Any] | None:
        row = get_db().execute(
            "SELECT * FROM fetch_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return _row_to_dict(row)

    def list_recent(self, limit: int = 20, status: str | None = None) -> list[dict[str, Any]]:
        if status:
            rows = get_db().execute(
                "SELECT * FROM fetch_jobs WHERE status = ? "
                "ORDER BY started_at DESC LIMIT ?",
                (status, limit),
            ).fetchall()
        else:
            rows = get_db().execute(
                "SELECT * FROM fetch_jobs ORDER BY started_at DESC LIMIT ?",
                (limit,),
            ).fetchall()
        result: list[dict[str, Any]] = []
        for r in rows:
            d = _row_to_dict(r)
            if d is not None:
                result.append(d)
        return result

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
