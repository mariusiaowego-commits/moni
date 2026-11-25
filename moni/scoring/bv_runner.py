"""On-demand general business-value scoring.

The share/brief pipeline scores an article as a side effect.  This module
lets the Pin list ask for one article's ten-dimension score directly, so
an article that never goes through a brief can still be scored.

One thread, one article at a time: a single article already costs up to
five model calls and 300 seconds of budget, so running two in parallel
would only make both slower.

``queued`` is written by the route before the article reaches the queue;
``running`` is written by the worker when it actually starts.  A row that
stays in either state past :data:`STALE_AFTER_SEC` is a job whose process
died, and is marked failed on the next request instead of blocking that
article forever.
"""

from __future__ import annotations

import logging
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from typing import Callable

from . import store
from .bv_pipeline import LLM, score_general_bv
from .snapshot import from_row

log = logging.getLogger(__name__)

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
INFLIGHT_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)

#: Budgets copied from the share pipeline so both paths behave the same.
DEFAULT_BUDGET_S = 300.0
DEFAULT_STEP_TIMEOUT = 180.0
#: A queued/running row older than this is treated as a dead job.
STALE_AFTER_SEC = 600

SELECT_ROW = (
    "SELECT id, title, raw_content, publisher, published_at, source, "
    "ai_summary, fact_check_conclusion, url, business_value_status, "
    "business_value_hash, business_value_rubric, business_value_at "
    "FROM articles WHERE id=?"
)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _stale_cutoff() -> str:
    return (datetime.now(timezone.utc) - timedelta(seconds=STALE_AFTER_SEC)).isoformat()


def load_row(conn: sqlite3.Connection, article_id: str):
    return conn.execute(SELECT_ROW, (article_id,)).fetchone()


def is_inflight(row) -> bool:
    try:
        return row["business_value_status"] in INFLIGHT_STATUSES
    except (KeyError, IndexError, TypeError):
        return False


def mark_queued(conn: sqlite3.Connection, article_id: str) -> bool:
    """Claim one article for scoring.

    Returns False when another request already owns it.  The cutoff is
    part of the ``WHERE`` clause so a dead row does not need a separate
    recovery pass to become claimable.
    """
    cur = conn.execute(
        "UPDATE articles SET business_value_status=?, business_value_error=NULL, "
        "business_value_at=? WHERE id=? AND (business_value_status IS NULL "
        "OR business_value_status NOT IN (?, ?) OR business_value_at IS NULL "
        "OR business_value_at < ?)",
        (STATUS_QUEUED, _now(), article_id, *INFLIGHT_STATUSES, _stale_cutoff()),
    )
    conn.commit()
    return cur.rowcount == 1


def mark_running(conn: sqlite3.Connection, article_id: str) -> bool:
    cur = conn.execute(
        "UPDATE articles SET business_value_status=? WHERE id=? AND business_value_status=?",
        (STATUS_RUNNING, article_id, STATUS_QUEUED),
    )
    conn.commit()
    return cur.rowcount == 1


def recover_stale(conn: sqlite3.Connection, live_ids: set[str]) -> int:
    """Mark interrupted rows failed so the article is scorable again."""
    placeholders = ",".join("?" for _ in INFLIGHT_STATUSES)
    rows = conn.execute(
        f"SELECT id FROM articles WHERE business_value_status IN ({placeholders}) "
        "AND (business_value_at IS NULL OR business_value_at < ?)",
        (*INFLIGHT_STATUSES, _stale_cutoff()),
    ).fetchall()
    stale = [row["id"] for row in rows if row["id"] not in live_ids]
    if not stale:
        return 0
    ids = ",".join("?" for _ in stale)
    conn.execute(
        f"UPDATE articles SET business_value_status='failed', "
        f"business_value_error='上次打分被中断（进程重启），请重新打分' "
        f"WHERE id IN ({ids})",
        tuple(stale),
    )
    conn.commit()
    return len(stale)


class GeneralBvRunner:
    """One thread, one article at a time."""

    def __init__(
        self,
        llm_factory: Callable[[str], LLM],
        *,
        budget_s: float = DEFAULT_BUDGET_S,
        step_timeout: float = DEFAULT_STEP_TIMEOUT,
    ):
        self._llm_factory = llm_factory
        self._budget_s = budget_s
        self._step_timeout = step_timeout
        self._lock = threading.Lock()
        self._pending: list[str] = []
        self._queued: set[str] = set()
        self._active: str | None = None
        self._thread: threading.Thread | None = None

    def submit(self, article_ids: list[str]) -> list[str]:
        """Queue ids, skipping ones already queued or running here."""
        accepted: list[str] = []
        with self._lock:
            for article_id in article_ids:
                if article_id in self._queued:
                    continue
                self._queued.add(article_id)
                self._pending.append(article_id)
                accepted.append(article_id)
            if accepted and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(
                    target=self._run_queue, name="bv-general-score", daemon=True
                )
                self._thread.start()
        return accepted

    def _run_queue(self) -> None:
        from moni.db.connection import close_db, get_db

        conn = get_db()
        try:
            while True:
                with self._lock:
                    if not self._pending:
                        self._active = None
                        return
                    article_id = self._pending.pop(0)
                    self._active = article_id
                try:
                    self._score(conn, article_id)
                except Exception:  # noqa: BLE001 - one crash must not kill the queue
                    log.exception("general bv score failed: %s", article_id)
                    _mark_crashed(conn, article_id)
                finally:
                    with self._lock:
                        self._queued.discard(article_id)
                        self._active = None
        finally:
            close_db()

    def _score(self, conn: sqlite3.Connection, article_id: str) -> None:
        row = load_row(conn, article_id)
        if row is None:
            log.warning("general bv score skipped, article gone: %s", article_id)
            return
        if not mark_running(conn, article_id):
            log.info("general bv score skipped, already claimed: %s", article_id)
            return
        snap = from_row(row)
        started = time.monotonic()
        result = score_general_bv(
            snap,
            llm=self._llm_factory(article_id),
            budget_s=self._budget_s,
            step_timeout=self._step_timeout,
        )
        store.write_bv(conn, snap, result)
        conn.commit()
        log.info(
            "general bv article=%s status=%s score=%s calls=%s elapsed=%.1fs",
            article_id,
            result.status,
            result.total,
            result.calls_used,
            time.monotonic() - started,
        )

    @property
    def active(self) -> str | None:
        with self._lock:
            return self._active

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def live_ids(self) -> set[str]:
        """Ids this process is going to finish.  Anything else is an orphan."""
        with self._lock:
            ids = set(self._queued)
            if self._active is not None:
                ids.add(self._active)
            return ids

    def wait_idle(self, timeout: float = 30.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self._lock:
                thread = self._thread
                idle = not self._pending and self._active is None
            if idle and thread is None:
                return True
            if idle and thread is not None and not thread.is_alive():
                thread.join(timeout=max(0.0, deadline - time.monotonic()))
                return not thread.is_alive()
            time.sleep(0.02)
        return False


def _mark_crashed(conn: sqlite3.Connection, article_id: str) -> None:
    try:
        conn.execute(
            "UPDATE articles SET business_value_status='failed', "
            "business_value_error='打分进程异常退出，请重新打分' WHERE id=?",
            (article_id,),
        )
        conn.commit()
    except sqlite3.OperationalError:
        log.exception("general bv crash mark failed: %s", article_id)


__all__ = [
    "GeneralBvRunner",
    "INFLIGHT_STATUSES",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STALE_AFTER_SEC",
    "is_inflight",
    "load_row",
    "mark_queued",
    "mark_running",
    "recover_stale",
]
