"""Background runner for one direction-research job at a time.

The runner does not use ``llm_jobs``: that queue is article-bound by a
foreign key, while direction research is configuration work.  This tiny
in-process queue keeps the research state machine auditable and leaves
no orphan subprocesses behind.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from typing import Callable

from . import direction_store
from .direction import build_clarify_prompt, build_research_prompt

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class ResearchRequest:
    direction_id: int
    research_version: int
    label: str
    brief: str
    #: The direction's own research guidance.  It is part of the research
    #: input, so the request carries the snapshot the run was started
    #: with rather than reading the direction again -- the row may have
    #: been edited while the run was queued.
    research_guidance: str = ""
    #: The reader perspective and the clarification Q&A the research is
    #: built from.  Both are part of the input, so they travel with the
    #: request like the guidance does.
    reader_perspective: str = ""
    clarification_answers: str = ""


def research_prompt(request: ResearchRequest) -> str:
    return build_research_prompt(
        request.label,
        request.brief,
        request.research_guidance,
        request.reader_perspective,
        request.clarification_answers,
    )


@dataclass(frozen=True)
class ClarificationRequest:
    direction_id: int
    clarification_version: int
    label: str
    brief: str
    reader_perspective: str


def clarification_prompt(request: ClarificationRequest) -> str:
    return build_clarify_prompt(
        request.label, request.brief, request.reader_perspective
    )


def run_clarification(
    request: ClarificationRequest,
    *,
    call_model: Callable[[str], str],
) -> str | None:
    """Run one clarification round synchronously and persist the outcome."""
    from moni.db.connection import get_db

    conn = get_db()
    try:
        try:
            raw = call_model(clarification_prompt(request))
        except Exception as exc:  # noqa: BLE001 - persisted, never raised
            log.warning("direction clarification %s failed: %s", request, exc)
            direction_store.finish_clarification_failure(
                conn,
                direction_id=request.direction_id,
                clarification_version=request.clarification_version,
                error=str(exc),
            )
            return str(exc)
        return direction_store.finish_clarification_success(
            conn,
            direction_id=request.direction_id,
            clarification_version=request.clarification_version,
            raw_clarification=raw,
        )[1]
    except Exception as exc:  # noqa: BLE001 - the queue must survive
        log.exception("direction clarification %s persistence failed", request)
        try:
            direction_store.finish_clarification_failure(
                conn,
                direction_id=request.direction_id,
                clarification_version=request.clarification_version,
                error=str(exc),
            )
        except Exception:
            log.exception(
                "direction clarification %s could not be marked failed", request
            )
        return str(exc)


def run_research(
    request: ResearchRequest,
    *,
    call_model: Callable[[str], str],
    model_name: Callable[[], str],
) -> str | None:
    """Run one research synchronously and persist the outcome.

    Returns the validation error when the research does not pass.  A
    transport error is persisted as a failed run rather than leaving
    the row stuck in ``running``.
    """
    from moni.db.connection import get_db

    conn = get_db()
    try:
        try:
            raw = call_model(research_prompt(request))
            model = model_name()
        except Exception as exc:  # noqa: BLE001 - persisted, never raised
            log.warning("direction research %s failed: %s", request, exc)
            direction_store.finish_research_failure(
                conn,
                direction_id=request.direction_id,
                research_version=request.research_version,
                error=str(exc),
            )
            return str(exc)
        return direction_store.finish_research_success(
            conn,
            direction_id=request.direction_id,
            research_version=request.research_version,
            raw_research=raw,
            model=model,
        )[1]
    except Exception as exc:  # noqa: BLE001 - the queue must survive
        log.exception("direction research %s persistence failed", request)
        try:
            direction_store.finish_research_failure(
                conn,
                direction_id=request.direction_id,
                research_version=request.research_version,
                error=str(exc),
            )
        except Exception:
            log.exception("direction research %s could not be marked failed", request)
        return str(exc)


class DirectionResearchRunner:
    """Single-worker, in-process queue for research requests."""

    def __init__(
        self,
        model_call: Callable[[str], str],
        model_name: Callable[[], str],
    ):
        self._model_call = model_call
        self._model_name = model_name
        self._lock = threading.Lock()
        self._pending: list[ResearchRequest] = []
        self._active: ResearchRequest | None = None
        self._thread: threading.Thread | None = None

    def submit(self, request: ResearchRequest) -> None:
        with self._lock:
            self._pending.append(request)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run_queue,
                    name="bv-direction-research",
                    daemon=True,
                )
                self._thread.start()

    def _run_queue(self) -> None:
        from moni.db.connection import close_db

        try:
            while True:
                with self._lock:
                    if not self._pending:
                        self._active = None
                        return
                    request = self._pending.pop(0)
                    self._active = request
                run_research(
                    request,
                    call_model=self._model_call,
                    model_name=self._model_name,
                )
        finally:
            close_db()

    @property
    def active(self) -> ResearchRequest | None:
        with self._lock:
            return self._active

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Wait for the worker thread to exit, including its cleanup."""
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


class DirectionClarificationRunner:
    """Single-worker, in-process queue for clarification requests.

    Same shape as the research runner: one call at a time, no orphan
    subprocesses, and a failed round is persisted as ``failed`` instead of
    leaving the row stuck in ``running``.
    """

    def __init__(self, model_call: Callable[[str], str]):
        self._model_call = model_call
        self._lock = threading.Lock()
        self._pending: list[ClarificationRequest] = []
        self._active: ClarificationRequest | None = None
        self._thread: threading.Thread | None = None

    def submit(self, request: ClarificationRequest) -> None:
        with self._lock:
            self._pending.append(request)
            if self._thread is None or not self._thread.is_alive():
                self._thread = threading.Thread(
                    target=self._run_queue,
                    name="bv-direction-clarification",
                    daemon=True,
                )
                self._thread.start()

    def _run_queue(self) -> None:
        from moni.db.connection import close_db

        try:
            while True:
                with self._lock:
                    if not self._pending:
                        self._active = None
                        return
                    request = self._pending.pop(0)
                    self._active = request
                run_clarification(request, call_model=self._model_call)
        finally:
            close_db()

    @property
    def active(self) -> ClarificationRequest | None:
        with self._lock:
            return self._active

    @property
    def pending_count(self) -> int:
        with self._lock:
            return len(self._pending)

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


__all__ = [
    "ClarificationRequest",
    "DirectionClarificationRunner",
    "DirectionResearchRunner",
    "ResearchRequest",
    "clarification_prompt",
    "research_prompt",
    "run_clarification",
    "run_research",
]
