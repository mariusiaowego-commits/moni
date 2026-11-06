"""FetchJobStore — async fetch job tracking (v1.0.7.6).

Used in /api/fetch/sources/{name}/run to return a job_id immediately while
the actual fetch runs in a background thread. The frontend polls
GET /api/fetch/jobs/{job_id} every 5s to render a progress bar.

Lifecycle: pending → running → (done | failed | timeout | stale | cancelled)

v1.0.42 (sprint-22): 心跳制 lease 取代 wall-clock 起点判死。
- 所有终态写走 ``moni.db.leases.finish(fenced)``;迟到写者天然被拒
- ``sweep_expired`` 取代 ``sweep_zombies``:基于 heartbeat,不再耦合 timeout 预算
- bestblogs timeout_sec=2100s 假警报的根因(``get_stale_threshold()``)
  随 ``STALE_THRESHOLD_SECONDS`` 一起删除

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
from ..db import leases


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# v1.0.42: 心跳 grace 窗口。grace = 3×心跳间隔 (90s)。
# 30s 心跳:bestblogs 整轮跑 880s 时,任意 30s 内必有心跳续约;
# sweep 只 reap 真僵尸(进程死掉/卡死不下心跳)。
DEFAULT_LEASE_GRACE_SEC = 90


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
        status: str = "running",
    ) -> str:
        """Insert a new job in running state. Returns the job id.

        If ``batch_id`` is provided, this job belongs to a batch (used by
        "全部抓取"). The frontend can poll ``get_batch(batch_id)`` to get
        aggregate progress across all jobs in the batch.

        Pass ``status="pending"`` for queued batch entries: pending rows carry
        no owner but do carry a heartbeat that the batch worker refreshes, so
        a queue whose worker died is reaped instead of blocking the batch.
        """
        job_id = str(uuid.uuid4())
        conn = get_db()
        # v1.0.42: write heartbeat at create time so the sweep doesn't
        # immediately reap a brand-new row before the worker thread
        # gets a chance to start heartbeating. Also write lease_owner so
        # the worker thread can adopt the lease via fence-bump without
        # having to re-acquire (which would race with sweep).
        job_owner = uuid.uuid4().hex if status == "running" else None
        conn.execute(
            """
            INSERT INTO fetch_jobs (
                id, source, status, total_feeds, done_feeds,
                articles_so_far, started_at, batch_id,
                lease_owner, lease_heartbeat_at
            ) VALUES (?, ?, ?, ?, 0, 0, ?, ?, ?,
                      strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            """,
            (
                job_id,
                source,
                status,
                total_feeds,
                _now_iso(),
                batch_id,
                job_owner,
            ),
        )
        conn.commit()
        return job_id

    def refresh_pending(self, batch_id: str) -> int:
        """Keep the heartbeat of queued rows alive while the batch runs.

        Returns the number of rows refreshed.
        """
        conn = get_db()
        cur = conn.execute(
            """
            UPDATE fetch_jobs
            SET lease_heartbeat_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE batch_id = ? AND status = 'pending'
            """,
            (batch_id,),
        )
        conn.commit()
        return cur.rowcount

    def fail_pending(self, batch_id: str, error: str) -> int:
        """Mark still-queued rows as failed (batch worker died)."""
        conn = get_db()
        cur = conn.execute(
            """
            UPDATE fetch_jobs
            SET status = 'failed',
                finished_at = ?,
                error = ?,
                current_feed = NULL
            WHERE batch_id = ? AND status = 'pending'
            """,
            (_now_iso(), error[:2000], batch_id),
        )
        conn.commit()
        return cur.rowcount

    def adopt(self, job_id: str) -> leases.LeaseHandle | None:
        """Start a queued (``pending``) job and return its lease handle.

        Batch fetches pre-create every source as pending; this flips one row
        to running, mints an owner and bumps the fence so the caller can
        heartbeat it and write a fenced terminal state.

        Returns ``None`` if the row is no longer pending (cancelled or reaped).
        """
        owner = uuid.uuid4().hex
        conn = get_db()
        row = conn.execute(
            """
            UPDATE fetch_jobs
            SET status = 'running',
                lease_owner = ?,
                lease_heartbeat_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now'),
                fence = fence + 1,
                started_at = strftime('%Y-%m-%dT%H:%M:%fZ', 'now')
            WHERE id = ? AND status = 'pending'
            RETURNING fence
            """,
            (owner, job_id),
        ).fetchone()
        conn.commit()
        if row is None:
            return None
        return leases.LeaseHandle(job_id=job_id, owner=owner, fence=int(row["fence"]))

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

    # ── terminal updates (v1.0.42: all fenced via leases.finish) ──────────

    def mark_done(
        self,
        job_id: str,
        result: dict[str, Any],
        handle: leases.LeaseHandle | None = None,
    ) -> bool:
        """Mark job done. Returns True if the write actually landed.

        If ``handle`` is provided (i.e. caller holds an active lease), the
        write is fenced and a late writer's stale write is rejected.
        If not provided, falls back to an unconditional write (used by
        legacy call sites that don't hold a lease — see ``mark_done_raw``).
        """
        if handle is None:
            # Legacy / cancel paths: no lease, just write.
            self._atomic_update(
                job_id,
                status="done",
                finished_at=_now_iso(),
                result_json=json.dumps(result, ensure_ascii=False, default=str),
                current_feed=None,
            )
            return True
        return leases.finish(
            handle,
            status="done",
            extra_fields={
                "finished_at": _now_iso(),
                "result_json": json.dumps(result, ensure_ascii=False, default=str),
                "current_feed": None,
            },
        )

    def mark_failed(
        self,
        job_id: str,
        error: str,
        handle: leases.LeaseHandle | None = None,
    ) -> bool:
        if handle is None:
            self._atomic_update(
                job_id,
                status="failed",
                finished_at=_now_iso(),
                error=error[:2000],
                current_feed=None,
            )
            return True
        return leases.finish(
            handle,
            status="failed",
            extra_fields={
                "finished_at": _now_iso(),
                "error": error[:2000],
                "current_feed": None,
            },
        )

    def mark_timeout(
        self,
        job_id: str,
        error: str | None = None,
        handle: leases.LeaseHandle | None = None,
    ) -> bool:
        if error is None:
            # Include how far the fetch actually got — a source that finished
            # a full sweep but ran past its budget is a "too slow" signal, not
            # a hard abort. v1.0.35.17: no more hardcoded "timeout after 10min".
            row = self.get_raw(job_id)
            n = (row or {}).get("articles_so_far") or 0
            error = f"超过超时预算 (已抓 {n} 篇)"
        if handle is None:
            self._atomic_update(
                job_id,
                status="timeout",
                finished_at=_now_iso(),
                error=error[:2000],
                current_feed=None,
            )
            return True
        return leases.finish(
            handle,
            status="timeout",
            extra_fields={
                "finished_at": _now_iso(),
                "error": error[:2000],
                "current_feed": None,
            },
        )

    def mark_stale(self, job_id: str, handle: leases.LeaseHandle | None = None) -> bool:
        """Mark a job as 'stale' (lease heartbeat presumed dead).

        Called by sweep_expired() when heartbeat < now-grace. The user-facing
        API surfaces this as an error so the SPA knows to prompt retry.
        """
        # Sweep path always passes handle=None — sweep_expired already did the
        # UPDATE directly. This method is also exposed for manual reap.
        if handle is None:
            self._atomic_update(
                job_id,
                status="stale",
                finished_at=_now_iso(),
                error=(
                    "服务进程中断或重启，任务已中止（心跳未续约）。"
                )[:2000],
                current_feed=None,
            )
            return True
        return leases.finish(
            handle,
            status="stale",
            extra_fields={
                "finished_at": _now_iso(),
                "error": (
                    "服务进程中断或重启，任务已中止（心跳未续约）。"
                )[:2000],
                "current_feed": None,
            },
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
        # Same as get()/list_recent(): reap dead rows first, otherwise an
        # orphaned batch (worker killed) would report "running" forever.
        self.sweep_zombies()
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
            # A source queued in a batch is not running yet, but starting it
            # again would run the same source twice.
            row = get_db().execute(
                "SELECT 1 FROM fetch_jobs "
                "WHERE status IN ('running', 'pending') AND source = ? LIMIT 1",
                (source,),
            ).fetchone()
        else:
            row = get_db().execute(
                "SELECT 1 FROM fetch_jobs WHERE status = 'running' LIMIT 1"
            ).fetchone()
        return row is not None

    def sweep_zombies(self) -> int:
        """Mark any 'running' job whose heartbeat has expired as 'stale'.

        v1.0.42: 重命名为 ``sweep_expired`` 的 thin wrapper 保留向后兼容;
        调用方应改用 ``leases.sweep_expired('fetch_jobs')`` 直接走心跳制。
        """
        return leases.sweep_expired("fetch_jobs", grace_sec=DEFAULT_LEASE_GRACE_SEC)

    # ── internals ──────────────────────────────────────────────────────────

    def _atomic_update(self, job_id: str, **fields: Any) -> int:
        """Raw UPDATE (no fence). For paths that don't hold an active lease.

        Returns rowcount.
        """
        if not fields:
            return 0
        cols = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [job_id]
        cur = get_db().execute(
            f"UPDATE fetch_jobs SET {cols} WHERE id = ?", params
        )
        get_db().commit()
        return cur.rowcount

    def _get_started_at(self, job_id: str) -> str | None:
        row = get_db().execute(
            "SELECT started_at FROM fetch_jobs WHERE id = ?", (job_id,)
        ).fetchone()
        return row["started_at"] if row else None
