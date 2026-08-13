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

import json
import logging
import re
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

from moni.db.connection import get_db
from moni.llm.client import call_llm
from moni.llm.prompts import render_fact_check, render_fact_check_plus, render_summary
# Re-export for backwards compat (Day 1 had DOMAIN_WHITELIST defined here;
# Day 2 moves the canonical definition to moni.web.routes.fact_check_plus).
# Some callers (e.g. tests) import from here.  Keep the symbol.
from moni.web.routes.fact_check_plus import (
    DOMAIN_WHITELIST,
    DOMAIN_WHITELIST_REGEX,
    apply_timeliness,
    compute_days_old,
    compute_timeliness,
    is_in_whitelist,
)

logger = logging.getLogger(__name__)

_VALID_TASK_TYPES = ("summary", "fact_check", "fact_check_plus")

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
    if task_type not in _VALID_TASK_TYPES:
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

    # v1.0.5.24: get article tags for perspective selection
    tag_rows = conn.execute(
        "SELECT tag_name FROM article_tags WHERE article_id = ?",
        (article_id,),
    ).fetchall()
    article_tags = [r["tag_name"] for r in tag_rows]

    # Pick the right prompt renderer for the job type.
    extra_args: list[str] | None = None
    try:
        if task_type == "summary":
            prompt = render_summary(article_dict, tags=article_tags)
        elif task_type == "fact_check":
            prompt = render_fact_check(article_dict)
        elif task_type == "fact_check_plus":
            prompt = render_fact_check_plus(article_dict)
            # hermes -t web agent mode (Q1=B); -Q quiet; cap turns.
            extra_args = ["-t", "web", "-Q", "--max-turns", "8"]
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
        result_text = call_llm(provider, model, prompt, extra_args=extra_args)
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
    # ``fact_checked_at`` + has_fact_check=1 so the same code path works.
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
                    fact_checked_at       = ?,
                    has_fact_check        = 1
                WHERE id = ?
                """,
                (result_text, "llm_inference", finished_at, article_id),
            )
        elif task_type == "fact_check_plus":
            _persist_fact_check_plus(
                conn,
                job_id=job_id,
                article_id=article_id,
                provider=provider,
                model=model,
                raw_output=result_text,
                finished_at=finished_at,
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


# ── fact_check_plus helpers ────────────────────────────────────────────────


def _domain_from_url(url: str) -> str:
    """Extract bare domain from URL (strip www.)."""
    try:
        host = (urlparse(url).netloc or "").lower()
    except Exception:
        return ""
    if host.startswith("www."):
        host = host[4:]
    return host


def _valid_url(url: str) -> bool:
    """True if url has http(s) scheme and a netloc."""
    try:
        p = urlparse(url)
    except Exception:
        return False
    return p.scheme in ("http", "https") and bool(p.netloc)


def _in_whitelist(domain: str) -> bool:
    """Dual-gate #2: domain exact/suffix match or regex whitelist hit."""
    d = (domain or "").lower().lstrip(".")
    if not d:
        return False
    if d in DOMAIN_WHITELIST:
        return True
    for w in DOMAIN_WHITELIST:
        if d == w or d.endswith("." + w):
            return True
    for pat in DOMAIN_WHITELIST_REGEX:
        if re.fullmatch(pat, d):
            return True
    return False


def _parse_fact_check_plus_evidence(raw: str) -> dict[str, Any] | None:
    """Parse LLM output into {conclusion, evidence: [...]} or None.

    Accepts raw JSON, fenced ```json blocks, or the first {...} object
    that contains an ``evidence`` array (regex extraction).
    """
    if not raw or not str(raw).strip():
        return None
    text = str(raw).strip()
    # Strip markdown fences
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)
    # 1) direct JSON
    try:
        obj = json.loads(text)
        if isinstance(obj, dict) and "evidence" in obj:
            return obj
    except json.JSONDecodeError:
        pass
    # 2) first JSON object containing "evidence"
    for m in re.finditer(r"\{[^{}]*(?:\{[^{}]*\}[^{}]*)*\}", text, re.DOTALL):
        candidate = m.group(0)
        if "evidence" not in candidate:
            continue
        try:
            obj = json.loads(candidate)
            if isinstance(obj, dict) and isinstance(obj.get("evidence"), list):
                return obj
        except json.JSONDecodeError:
            continue
    # 3) greedy outer braces from first { to last }
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict) and isinstance(obj.get("evidence"), list):
                return obj
        except json.JSONDecodeError:
            pass
    return None


# B5 parse-failure reasons that get raw_output logged into llm_jobs.error.
_FACT_CHECK_PLUS_PARSE_REASONS = frozenset(
    {
        "evidence parse failed",
        "evidence parse failed: evidence is not a list",
        "no evidence with snippet",
    }
)


def _truncate_utf8(s: str, max_bytes: int) -> str:
    """Truncate string to max_bytes without splitting a multi-byte UTF-8 char."""
    encoded = (s or "").encode("utf-8")[:max_bytes]
    return encoded.decode("utf-8", errors="ignore")


def _log_failure(conn: Any, job_id: str, raw_output: str, parse_reason: str) -> str:
    """B5: persist truncated LLM raw_output into llm_jobs.error for diagnosis.

    Returns the error message (also written to DB) so callers can re-raise it
    and survive the outer ``_mark_failed`` overwrite of the error column.
    """
    raw = raw_output or ""
    truncated = _truncate_utf8(raw, 2048)
    total_bytes = len(raw.encode("utf-8"))
    suffix = "" if total_bytes <= 2048 else f" ... [truncated {total_bytes}B total]"
    err_msg = f"RAW OUTPUT: {truncated}{suffix} | PARSE: {parse_reason}"
    conn.execute(
        "UPDATE llm_jobs SET error = ? WHERE id = ?",
        (err_msg, job_id),
    )
    conn.commit()
    return err_msg


def _persist_fact_check_plus(
    conn: Any,
    *,
    job_id: str,
    article_id: str,
    provider: str,
    model: str,
    raw_output: str,
    finished_at: str,
) -> None:
    """Parse + 闸 1/2/3 + write articles + fact_check_evidence rows.

    闸 3 (v1.0.26): days_old 31-90 结论加 ``(已过期 30-90 天)``;
    91+ 强制【存疑】. 取本批最大 days_old (最严).

    Raises ValueError on parse / empty-evidence so caller marks job failed.
    On the three parse-failure branches, logs truncated raw_output into
    llm_jobs.error (B5) before re-raising.
    """
    try:
        parsed = _parse_fact_check_plus_evidence(raw_output)
        if not parsed:
            raise ValueError("evidence parse failed")

        raw_evidence = parsed.get("evidence") or []
        if not isinstance(raw_evidence, list):
            raise ValueError("evidence parse failed: evidence is not a list")

        # Gate 1: drop rows without snippet / invalid URL
        valid: list[dict[str, Any]] = []
        for e in raw_evidence:
            if not isinstance(e, dict):
                continue
            url = (e.get("url") or "").strip()
            snippet = (e.get("snippet") or "").strip()
            if not url or not snippet or not _valid_url(url):
                continue
            domain = (e.get("domain") or "").strip() or _domain_from_url(url)
            ev_fetched = (e.get("fetched_at") or "").strip() or None
            valid.append(
                {
                    "query": (e.get("query") or "").strip() or "",
                    "url": url,
                    "domain": domain,
                    "snippet": snippet,
                    "rationale": (e.get("rationale") or "").strip() or "",
                    "fetched_at": ev_fetched,
                }
            )
        if not valid:
            raise ValueError("no evidence with snippet")

        # Gate 2: whitelist required for 【已核实】
        conclusion = str(parsed.get("conclusion") or "【待核实】").strip()
        has_whitelist = any(is_in_whitelist(e["domain"]) for e in valid)
        if conclusion == "【已核实】" and not has_whitelist:
            conclusion = "【待核实】"
            logger.info(
                "[llm_jobs] fact_check_plus job %s: demoted 【已核实】→【待核实】 (no whitelist domain)",
                job_id,
            )

        # Gate 3: timeliness — 31-90 加后缀, 91+ 强制【存疑】
        now_dt = datetime.now(timezone.utc)
        worst_days: int | None = None
        for e in valid:
            raw_fetched = e.get("fetched_at") or finished_at
            e["fetched_at"] = raw_fetched
            days_old = compute_days_old(raw_fetched, now=now_dt)
            e["days_old"] = days_old
            if days_old is not None and (worst_days is None or days_old > worst_days):
                worst_days = days_old
        timeliness = compute_timeliness(worst_days)
        conclusion = apply_timeliness(conclusion, worst_days)
        if timeliness == "90d_demote":
            logger.info(
                "[llm_jobs] fact_check_plus job %s: evidence days_old=%s → 90d_demote 【存疑】",
                job_id,
                worst_days,
            )
        elif timeliness == "30d_warn":
            logger.info(
                "[llm_jobs] fact_check_plus job %s: evidence days_old=%s → 30d_warn suffix",
                job_id,
                worst_days,
            )

        note = json.dumps(valid, ensure_ascii=False)
        try:
            conn.execute(
                """
                UPDATE articles
                SET fact_check_conclusion  = ?,
                    fact_check_method      = 'web_search',
                    fact_checked_at        = ?,
                    fact_check_note        = ?,
                    fact_check_timeliness  = ?,
                    has_fact_check         = 1,
                    has_web_fact_check     = 1
                WHERE id = ?
                """,
                (conclusion, finished_at, note, timeliness, article_id),
            )
        except Exception:
            # Pre-migration 026/027: optional columns may be missing.
            try:
                conn.execute(
                    """
                    UPDATE articles
                    SET fact_check_conclusion = ?,
                        fact_check_method     = 'web_search',
                        fact_checked_at       = ?,
                        fact_check_note       = ?,
                        fact_check_timeliness = ?,
                        has_fact_check        = 1
                    WHERE id = ?
                    """,
                    (conclusion, finished_at, note, timeliness, article_id),
                )
            except Exception:
                conn.execute(
                    """
                    UPDATE articles
                    SET fact_check_conclusion = ?,
                        fact_check_method     = 'web_search',
                        fact_checked_at       = ?,
                        fact_check_note       = ?,
                        has_fact_check        = 1
                    WHERE id = ?
                    """,
                    (conclusion, finished_at, note, article_id),
                )

        # Replace prior evidence for this article (re-run overwrite).
        conn.execute(
            "DELETE FROM fact_check_evidence WHERE article_id = ?",
            (article_id,),
        )
        for i, e in enumerate(valid):
            row_id = uuid.uuid4().hex
            vals = (
                row_id,
                article_id,
                job_id,
                e["query"],
                e["url"],
                e["domain"],
                e["snippet"],
                e.get("fetched_at") or finished_at,
                model,
                provider,
                conclusion,
                e.get("rationale") or "",
                i,
                finished_at,
            )
            try:
                conn.execute(
                    """
                    INSERT INTO fact_check_evidence
                        (id, article_id, job_id, query, url, domain, snippet,
                         fetched_at, model, provider, conclusion, rationale,
                         evidence_seq, created_at, days_old)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    vals + (e.get("days_old"),),
                )
            except Exception:
                # Pre-migration 027: days_old column may be missing.
                conn.execute(
                    """
                    INSERT INTO fact_check_evidence
                        (id, article_id, job_id, query, url, domain, snippet,
                         fetched_at, model, provider, conclusion, rationale,
                         evidence_seq, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    vals,
                )
    except ValueError as exc:
        # B5: log truncated raw_output into llm_jobs.error for the 3 parse
        # failure branches; re-raise with full message so outer _mark_failed
        # does not clobber the diagnostic payload.
        if str(exc) in _FACT_CHECK_PLUS_PARSE_REASONS:
            msg = _log_failure(conn, job_id, raw_output, str(exc))
            raise ValueError(msg) from None
        raise


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
