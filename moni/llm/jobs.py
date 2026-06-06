"""LLM job worker — background thread that drains ``llm_jobs`` (v1.0.5.1).

Why this exists
---------------
Pre-v1.0.5.1, ``POST /api/articles/{id}/summary`` and ``/fact-check``
called ``hermes chat`` synchronously inside the request handler.  That
had two problems:

1. The HTTP request blocked for up to 60s (the v1.0 LLM timeout) before
   returning, leaving the SPA spinner frozen with no progress signal.
2. When the LLM failed (timeout, network, parse error) the user saw
   the raw error message in a modal alert and the work was lost — no
   retry, no audit trail, no way to come back to it.

This module fixes both: the route enqueues a row in ``llm_jobs`` and
returns 202 immediately; a background ``threading.Thread`` running
``drain_pending_jobs`` (started lazily on first enqueue, lives for the
process lifetime) picks up pending rows, calls the LLM, and writes
status / result / error back to the DB.

Architecture notes
------------------
* One worker per process, started by ``ensure_worker_started()`` (called
  from the article routes).  A module-level ``_WORKER_LOCK`` serialises
  startup so a burst of enqueue requests still spawns only one thread.
* We use ``threading`` (not ``multiprocessing``) because the work is
  I/O-bound (subprocess + DB writes) and we need to share the same
  SQLite ``get_db()`` connection pool.  SQLite + threads is safe with
  ``check_same_thread=False`` (the connection module is configured that
  way).
* Backoff: 2s sleep between empty polls.  When a job is found, the
  worker runs it inline then immediately polls again.
* Retries: the route sets ``attempts=0``; the worker increments it on
  every call to ``hermes chat``.  If a call raises, the worker re-queues
  the same row with ``status='pending'`` until ``attempts >= 2``, at
  which point it marks the job ``failed`` and moves on.  The 2 means
  "1 original + 1 retry", matching the user's request in the v1.0.5.1
  plan.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from moni.db.connection import get_db
from moni.llm.client import call_llm
from moni.llm.prompts import render_fact_check, render_summary

logger = logging.getLogger(__name__)

#: How many times we'll try a single LLM call before giving up.
#: 1 = no retry; 2 = original + 1 retry.  Pinned to 2 by v1.0.5.1.
MAX_ATTEMPTS: int = 2

#: How long to sleep between polls when the queue is empty.
_POLL_INTERVAL_SECONDS: float = 2.0

#: Module-level lock + flag ensuring we spawn exactly one worker thread.
_WORKER_LOCK = threading.Lock()
_WORKER_THREAD: threading.Thread | None = None
_WORKER_STOP = threading.Event()


# ── enqueue / inspect helpers ──────────────────────────────────────────────


def enqueue_job(
    *,
    task_type: str,
    article_id: str,
    provider: str,
    model: str,
) -> str:
    """Insert a pending job row and return its id.  Starts the worker."""
    if task_type not in ("summary", "fact_check"):
        raise ValueError(f"unknown task_type {task_type!r}")

    job_id = uuid.uuid4().hex
    now = datetime.now(timezone.utc).isoformat()
    conn = get_db()
    conn.execute(
        """
        INSERT INTO llm_jobs
            (id, task_type, article_id, provider, model,
             status, attempts, created_at)
        VALUES (?, ?, ?, ?, ?, 'pending', 0, ?)
        """,
        (job_id, task_type, article_id, provider, model, now),
    )
    conn.commit()
    logger.info(
        "[llm_jobs] enqueued %s job %s for article %s (%s/%s)",
        task_type, job_id, article_id, provider, model,
    )
    ensure_worker_started()
    return job_id


def get_job(job_id: str) -> dict[str, Any] | None:
    """Fetch a single job row, or None if missing."""
    conn = get_db()
    row = conn.execute(
        "SELECT * FROM llm_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    return dict(row) if row else None


def list_jobs(
    *,
    status: str | None = None,
    article_id: str | None = None,
    limit: int = 100,
) -> list[dict[str, Any]]:
    """List jobs, newest first, optionally filtered by status and article."""
    conn = get_db()
    where: list[str] = []
    params: list[Any] = []
    if status:
        where.append("status = ?")
        params.append(status)
    if article_id:
        where.append("article_id = ?")
        params.append(article_id)
    where_sql = ("WHERE " + " AND ".join(where)) if where else ""
    params.append(limit)
    rows = conn.execute(
        f"""
        SELECT j.*, a.title AS article_title
        FROM llm_jobs j
        LEFT JOIN articles a ON a.id = j.article_id
        {where_sql}
        ORDER BY j.created_at DESC
        LIMIT ?
        """,
        params,
    ).fetchall()
    return [dict(r) for r in rows]


# ── worker loop ────────────────────────────────────────────────────────────


def ensure_worker_started() -> None:
    """Start the singleton worker thread if it isn't already running."""
    global _WORKER_THREAD
    if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
        return
    with _WORKER_LOCK:
        if _WORKER_THREAD is not None and _WORKER_THREAD.is_alive():
            return
        _WORKER_STOP.clear()
        _WORKER_THREAD = threading.Thread(
            target=_worker_loop,
            name="moni-llm-jobs-worker",
            daemon=True,
        )
        _WORKER_THREAD.start()
        logger.info("[llm_jobs] worker thread started")


def stop_worker() -> None:
    """Signal the worker to exit (used in tests / shutdown)."""
    _WORKER_STOP.set()


def _worker_loop() -> None:
    """Drain the queue until ``_WORKER_STOP`` is set."""
    logger.info("[llm_jobs] worker loop entered")
    while not _WORKER_STOP.is_set():
        try:
            processed = _process_one_pending()
        except Exception as exc:
            # Defensive: never let the worker die because of an unexpected
            # error in DB / model code.  Log and back off.
            logger.exception("[llm_jobs] worker loop error: %s", exc)
            processed = False
        if not processed:
            # Idle — short sleep, then poll again.
            _WORKER_STOP.wait(_POLL_INTERVAL_SECONDS)
    logger.info("[llm_jobs] worker loop exiting")


def _process_one_pending() -> bool:
    """Try to claim and run one pending job.  Return True if we did.

    v1.0.5.1: respect ``cancelled`` rows.  If a user cancels a job while
    it's still pending, the worker used to pick it up anyway.  Now we
    sweep cancelled-while-pending rows aside as a separate "transition"
    step before the main claim, so the worker can't grab a row the user
    already abandoned.
    """
    conn = get_db()

    # 1. Drop any pending row that the user has cancelled.
    cancelled = conn.execute(
        """
        SELECT id FROM llm_jobs
        WHERE status = 'pending' AND id IN (
            SELECT id FROM llm_jobs
            WHERE status = 'cancelled'
        )
        """
    ).fetchall()
    if cancelled:
        for r in cancelled:
            conn.execute("DELETE FROM llm_jobs WHERE id = ?", (r["id"],))
        conn.commit()
        logger.info(
            "[llm_jobs] swept %d cancelled-while-pending row(s)", len(cancelled)
        )

    # 2. Atomic-ish claim: SELECT a pending row, flip to running, commit.
    #    Two workers would race here, but we run exactly one per process.
    row = conn.execute(
        """
        SELECT id, task_type, article_id, provider, model, attempts
        FROM llm_jobs
        WHERE status = 'pending'
        ORDER BY created_at ASC
        LIMIT 1
        """
    ).fetchone()
    if row is None:
        return False

    job_id = row["id"]
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE llm_jobs SET status = 'running', started_at = ? WHERE id = ?",
        (now, job_id),
    )
    conn.commit()

    _run_job(dict(row))
    return True


def _run_job(row: dict[str, Any]) -> None:
    """Execute a single LLM call for a claimed job, write the outcome."""
    job_id: str = row["id"]
    task_type: str = row["task_type"]
    article_id: str = row["article_id"]
    provider: str = row["provider"]
    model: str = row["model"]
    attempt: int = int(row["attempts"] or 0) + 1

    conn = get_db()
    article_row = conn.execute(
        "SELECT * FROM articles WHERE id = ?", (article_id,)
    ).fetchone()
    if article_row is None:
        # Article was deleted between enqueue and run — fail cleanly.
        _mark_failed(job_id, f"article {article_id!r} not found", attempts=attempt)
        return

    article_dict = dict(article_row)

    # Pick the right prompt renderer for the job type.
    try:
        if task_type == "summary":
            prompt = render_summary(article_dict)
        elif task_type == "fact_check":
            prompt = render_fact_check(article_dict)
        else:
            _mark_failed(job_id, f"unsupported task_type {task_type!r}", attempts=attempt)
            return
    except Exception as exc:
        _mark_failed(job_id, f"prompt render failed: {exc}", attempts=attempt)
        return

    # Make the LLM call (already retried internally? No — we do our own
    # retry here so attempts are visible in the DB).
    t0 = time.monotonic()
    try:
        result_text = call_llm(provider, model, prompt)
    except Exception as exc:
        duration_ms = int((time.monotonic() - t0) * 1000)
        err_msg = f"{type(exc).__name__}: {exc}"
        if attempt < MAX_ATTEMPTS:
            # Re-queue for a retry.
            logger.warning(
                "[llm_jobs] job %s attempt %d/%d failed (%s) — requeueing",
                job_id, attempt, MAX_ATTEMPTS, err_msg,
            )
            conn.execute(
                """
                UPDATE llm_jobs
                SET status = 'pending',
                    attempts = ?,
                    error = ?,
                    duration_ms = ?
                WHERE id = ?
                """,
                (attempt, err_msg, duration_ms, job_id),
            )
            conn.commit()
        else:
            _mark_failed(job_id, err_msg, attempts=attempt, duration_ms=duration_ms)
        return

    duration_ms = int((time.monotonic() - t0) * 1000)

    # Persist the LLM output.  For summary jobs we write back into
    # ``articles.summary`` / ``articles.summary_at`` so the existing UI
    # can show the result.  For fact_check we write the columns
    # ``fact_check_conclusion`` / ``fact_check_method`` /
    # ``fact_checked_at`` so the same code path works for both.
    finished_at = datetime.now(timezone.utc).isoformat()
    try:
        if task_type == "summary":
            conn.execute(
                """
                UPDATE articles
                SET summary = ?, summary_at = ?
                WHERE id = ?
                """,
                (result_text, finished_at, article_id),
            )
        elif task_type == "fact_check":
            conn.execute(
                """
                UPDATE articles
                SET fact_check_conclusion = ?,
                    fact_check_method     = ?,
                    fact_checked_at       = ?
                WHERE id = ?
                """,
                (result_text, "llm", finished_at, article_id),
            )
    except Exception as exc:
        _mark_failed(
            job_id,
            f"DB writeback failed: {exc}",
            attempts=attempt,
            duration_ms=duration_ms,
        )
        return

    # Honour a user-initiated cancel that arrived while the LLM was running.
    # We don't want to overwrite a "cancelled" status with "done" — that
    # would confuse the user into thinking the work was completed.
    current = conn.execute(
        "SELECT status FROM llm_jobs WHERE id = ?", (job_id,)
    ).fetchone()
    if current and current["status"] == "cancelled":
        logger.info(
            "[llm_jobs] job %s was cancelled during LLM call; discarding result",
            job_id,
        )
        return

    conn.execute(
        """
        UPDATE llm_jobs
        SET status      = 'done',
            result      = ?,
            attempts    = ?,
            duration_ms = ?,
            finished_at = ?
        WHERE id = ?
        """,
        (result_text, attempt, duration_ms, finished_at, job_id),
    )
    conn.commit()
    logger.info(
        "[llm_jobs] job %s done (%s, %d ms, attempt %d)",
        job_id, task_type, duration_ms, attempt,
    )


def _mark_failed(
    job_id: str,
    error_msg: str,
    *,
    attempts: int,
    duration_ms: int | None = None,
) -> None:
    """Mark a job as failed with the given error message."""
    conn = get_db()
    finished_at = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        UPDATE llm_jobs
        SET status      = 'failed',
            error       = ?,
            attempts    = ?,
            duration_ms = COALESCE(?, duration_ms),
            finished_at = ?
        WHERE id = ?
        """,
        (error_msg, attempts, duration_ms, finished_at, job_id),
    )
    conn.commit()
    logger.warning("[llm_jobs] job %s failed: %s", job_id, error_msg)
