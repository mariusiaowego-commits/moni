"""Single-worker queue for direction scoring (Sprint 67, S3).

Scoring one article costs several model calls, so requests run one at a
time.  The queue is in-process and deliberately small: it exists to keep
an HTTP request from blocking on the model, not to be a durable job store.

Two states matter here.  The API writes ``queued`` before the request
reaches this class; the worker writes ``running`` when it starts that
article.  A queue of twelve articles therefore shows twelve ``queued``
rows and one ``running`` row, which is what the UI polls.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Iterable

from . import direction_score

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ScoreRequest:
    direction_id: int
    research_version: int
    rubric: Any
    article_id: str

    @property
    def key(self) -> tuple[str, int, int]:
        return (self.article_id, self.direction_id, self.research_version)


class DirectionScoreRunner:
    """One thread, one article at a time."""

    def __init__(
        self,
        llm_factory: Callable[[], Any],
        *,
        budget_s: float = direction_score.DEFAULT_BUDGET_S,
        step_timeout: float = direction_score.DEFAULT_STEP_TIMEOUT,
    ):
        self._llm_factory = llm_factory
        self._budget_s = budget_s
        self._step_timeout = step_timeout
        self._lock = threading.Lock()
        self._pending: list[ScoreRequest] = []
        self._queued: set[tuple[str, int, int]] = set()
        self._active: ScoreRequest | None = None
        self._thread: threading.Thread | None = None

    def submit(self, requests: Iterable[ScoreRequest]) -> list[ScoreRequest]:
        """Queue requests, skipping ones already queued or running."""
        accepted: list[ScoreRequest] = []
        with self._lock:
            for req in requests:
                if req.key in self._queued:
                    continue
                self._queued.add(req.key)
                self._pending.append(req)
                accepted.append(req)
            if accepted and (self._thread is None or not self._thread.is_alive()):
                self._thread = threading.Thread(
                    target=self._run_queue,
                    name="bv-direction-score",
                    daemon=True,
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
                    request = self._pending.pop(0)
                    self._active = request
                try:
                    self._score(conn, request)
                except Exception:  # noqa: BLE001 - a worker crash must not kill the queue
                    log.exception("direction score failed: %s", request.key)
                finally:
                    with self._lock:
                        self._queued.discard(request.key)
                        self._active = None
        finally:
            close_db()

    def _score(self, conn, request: ScoreRequest) -> None:
        row = conn.execute(
            "SELECT * FROM articles WHERE id=?", (request.article_id,)
        ).fetchone()
        if row is None:
            log.warning("direction score skipped, article gone: %s", request.article_id)
            return
        claimed = direction_score.mark_running(
            conn,
            article_id=request.article_id,
            direction_id=request.direction_id,
            research_version=request.research_version,
        )
        if not claimed:
            log.info("direction score skipped, already claimed: %s", request.key)
            return
        outcome = direction_score.score_one(
            conn,
            direction_id=request.direction_id,
            research_version=request.research_version,
            rubric=request.rubric,
            article_row=row,
            llm=self._llm_factory(),
            budget_s=self._budget_s,
            step_timeout=self._step_timeout,
        )
        log.info(
            "direction score article=%s direction=%s status=%s score=%s",
            request.article_id,
            request.direction_id,
            outcome.get("status"),
            outcome.get("score"),
        )

    @property
    def active(self) -> ScoreRequest | None:
        with self._lock:
            return self._active

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    @property
    def live_keys(self) -> set[tuple[str, int, int]]:
        """Keys this process is going to finish.  Anything else is an orphan."""
        with self._lock:
            keys = set(self._queued)
            if self._active is not None:
                keys.add(self._active.key)
            return keys

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


__all__ = ["DirectionScoreRunner", "ScoreRequest"]
