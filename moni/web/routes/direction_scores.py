"""Direction scoring API (Sprint 67, S3).

Scoring is explicit: nothing here runs unless the user asks for it.  A
POST queues at most twelve articles; the worker scores them one at a
time and the client polls the GET endpoint until every article left the
queue.

The API never returns ``rubric_json`` -- only the dimension breakdown
that was already produced by the scoring harness.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.scoring import direction_score
from moni.scoring.direction_score_runner import DirectionScoreRunner, ScoreRequest
from moni.web.exceptions import BadRequestError, ConflictError, NotFoundError
from moni.web.models import (
    DirectionScoreBatch,
    DirectionScoreItem,
    DirectionScoreQueued,
    DirectionScoresResponse,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/directions", tags=["directions"])

_runner: DirectionScoreRunner | None = None
_runner_lock = threading.Lock()
#: Serialises the queue-critical section.
#:
#: ``mark_queued`` writes a ``queued`` row before the runner knows about
#: it, and ``recover_stale`` treats a queued row the runner does not know
#: as an orphan left over from a restart.  Without this lock the two can
#: interleave: one request queues an article, another request's recovery
#: pass marks it failed, and the article is never scored even though the
#: API answered ``202``.
_enqueue_lock = threading.Lock()


def _llm_call(prompt: str, timeout: float) -> tuple[str, int]:
    """Adapter for the harness' ``llm(prompt, timeout) -> (text, exit code)``.

    ``call_llm`` raises on provider errors; the harness expects a return
    value instead, so every failure becomes ``("", 1)`` and the article
    ends up as ``failed`` with a real reason.
    """
    from moni.llm.client import call_llm, resolve_active_model
    from moni.llm.runner import read_model_mode

    if read_model_mode() == "profile":
        provider, model = "", ""
    else:
        provider, model = resolve_active_model()
    try:
        out = call_llm(
            provider,
            model,
            prompt,
            timeout=max(15, int(round(timeout))),
        )
    except Exception as exc:  # noqa: BLE001 - provider failures are data here
        log.warning("direction score llm failed: %s", exc)
        return "", 1
    if not isinstance(out, str):
        return "", 1
    return out, 0


def _ensure_runner() -> DirectionScoreRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = DirectionScoreRunner(lambda: _llm_call)
        return _runner


def _resolve(direction_id: int) -> tuple[Any, Any, Any]:
    """Return ``(research, rubric, reason)``; ``reason`` set means unusable."""
    conn = get_db()
    direction, research, rubric, reason = direction_score.resolve_rubric(
        conn, direction_id
    )
    if direction is None:
        raise NotFoundError(message=reason or "方向不存在")
    if rubric is None:
        raise ConflictError(message=reason or "方向不可用")
    return research, rubric, None


def _parse_ids(raw: str) -> list[str]:
    out: list[str] = []
    for part in (raw or "").split(","):
        value = part.strip()
        if value and value not in out:
            out.append(value)
    return out


@router.get("/{direction_id}/scores", response_model=DirectionScoresResponse)
def get_direction_scores(
    direction_id: int,
    article_ids: str = Query(default="", description="Comma-separated article ids"),
) -> DirectionScoresResponse:
    research, rubric, _ = _resolve(direction_id)
    ids = _parse_ids(article_ids)
    if not ids:
        raise BadRequestError(message="article_ids 不能为空")
    if len(ids) > direction_score.MAX_REQUEST:
        raise BadRequestError(
            message=f"一次最多查询 {direction_score.MAX_REQUEST} 篇"
        )
    conn = get_db()
    runner = _ensure_runner()
    with _enqueue_lock:
        direction_score.recover_stale(conn, live_keys=runner.live_keys)
    scores = direction_score.load_scores(
        conn,
        direction_id=direction_id,
        research_version=research.version,
        article_ids=ids,
    )
    return DirectionScoresResponse(
        direction_id=direction_id,
        research_version=research.version,
        rubric_id=rubric.id,
        scores={
            article_id: DirectionScoreItem(**payload)
            for article_id, payload in scores.items()
        },
    )


@router.post(
    "/{direction_id}/scores", response_model=DirectionScoreQueued, status_code=202
)
def start_direction_scores(
    direction_id: int, payload: DirectionScoreBatch
) -> DirectionScoreQueued:
    research, rubric, _ = _resolve(direction_id)
    ids: list[str] = []
    for raw in payload.article_ids:
        value = (raw or "").strip()
        if value and value not in ids:
            ids.append(value)
    if not ids:
        raise BadRequestError(message="article_ids 不能为空")
    if len(ids) > direction_score.MAX_REQUEST:
        raise BadRequestError(
            message=f"一次最多提交 {direction_score.MAX_REQUEST} 篇"
        )

    conn = get_db()
    runner = _ensure_runner()
    cached: list[str] = []
    inflight: list[str] = []
    rejected: list[dict[str, str]] = []
    queued: list[str] = []
    with _enqueue_lock:
        direction_score.recover_stale(conn, live_keys=runner.live_keys)
        hashes = direction_score.article_hashes(conn, ids)
        existing = direction_score.load_scores(
            conn,
            direction_id=direction_id,
            research_version=research.version,
            article_ids=ids,
        )

        todo: list[str] = []
        for article_id in ids:
            if article_id not in hashes:
                rejected.append({"id": article_id, "reason": "文章不存在"})
                continue
            row = existing.get(article_id)
            if row and not row["stale"]:
                status = row["status"]
                if status in direction_score.SETTLED_STATUSES:
                    cached.append(article_id)
                    continue
                if status in direction_score.INFLIGHT_STATUSES:
                    inflight.append(article_id)
                    continue
            todo.append(article_id)

        if len(todo) > direction_score.MAX_BATCH:
            raise BadRequestError(
                message=(
                    f"待打分文章 {len(todo)} 篇，超过单批上限 "
                    f"{direction_score.MAX_BATCH} 篇，请分次触发"
                )
            )

        requests: list[ScoreRequest] = []
        for article_id in todo:
            claimed = direction_score.mark_queued(
                conn,
                article_id=article_id,
                direction_id=direction_id,
                research_version=research.version,
                body_hash=hashes[article_id],
                rubric_id=rubric.id,
            )
            if not claimed:
                rejected.append({"id": article_id, "reason": "该文章正在打分中"})
                continue
            requests.append(
                ScoreRequest(
                    direction_id=direction_id,
                    research_version=research.version,
                    rubric=rubric,
                    article_id=article_id,
                )
            )
        accepted = runner.submit(requests)
        queued = [request.article_id for request in accepted]
    return DirectionScoreQueued(
        direction_id=direction_id,
        research_version=research.version,
        rubric_id=rubric.id,
        queued=queued,
        cached=cached + inflight,
        rejected=rejected,
    )
