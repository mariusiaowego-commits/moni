"""Business-direction configuration and research API (Sprint 67, S2).

The API deliberately never accepts or returns ``rubric_json``.  A
direction's question set is produced by the fixed research prompt and
validated by ``moni.scoring.direction``; exposing the raw rubric would
invite hand edits that bypass that gate.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from fastapi import APIRouter

from moni.db.connection import get_db
from moni.scoring import direction_store
from moni.scoring.direction import (
    DIRECTION_PROMPT_ID,
    STATUS_RUNNING,
)
from moni.scoring.direction_runner import (
    DirectionResearchRunner,
    ResearchRequest,
)
from moni.web.exceptions import BadRequestError, ConflictError, NotFoundError
from moni.web.models import (
    Direction,
    DirectionCreate,
    DirectionResearchAction,
    DirectionUpdate,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/directions", tags=["directions"])

_runner: DirectionResearchRunner | None = None
_runner_lock = threading.Lock()


def _research_action(row: dict[str, Any] | None) -> DirectionResearchAction | None:
    if row is None:
        return None
    return DirectionResearchAction(
        direction_id=int(row["direction_id"]),
        research_version=int(row["research_version"]),
        status=str(row["status"]),
    )


def _direction_response(direction: dict[str, Any]) -> Direction:
    conn = get_db()
    direction_store.recover_stale_running(conn)
    research = direction_store.research_summary(conn, direction)
    usable, usable_error = direction_store.direction_usable(conn, direction)
    research_row = None
    if research is not None:
        research_row = {
            "direction_id": int(direction["id"]),
            "research_version": int(research["research_version"]),
            "status": research["status"],
        }
    return Direction(
        id=int(direction["id"]),
        label=str(direction["label"]),
        brief=str(direction["brief"]),
        enabled=bool(direction["enabled"]),
        created_at=str(direction["created_at"]),
        updated_at=str(direction["updated_at"]),
        research=_research_action(research_row),
        usable=usable is not None,
        usable_error=usable_error,
    )


def _get_direction_or_404(direction_id: int) -> dict[str, Any]:
    conn = get_db()
    direction = direction_store.get_direction(conn, direction_id)
    if direction is None:
        raise NotFoundError(message="方向不存在")
    return direction


def _model_call(prompt: str) -> str:
    from moni.llm.client import call_llm, resolve_active_model

    provider, model = resolve_active_model()
    return call_llm(provider, model, prompt)


def _ensure_runner() -> DirectionResearchRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = DirectionResearchRunner(_model_call, "active-summary-model")
        return _runner


@router.get("", response_model=list[Direction])
def list_directions() -> list[Direction]:
    conn = get_db()
    direction_store.recover_stale_running(conn)
    return [_direction_response(direction) for direction in direction_store.list_directions(conn)]


@router.post("", response_model=Direction, status_code=201)
def create_direction(payload: DirectionCreate) -> Direction:
    conn = get_db()
    direction_id, error = direction_store.create_direction(conn, payload.label, payload.brief)
    if direction_id is None:
        raise BadRequestError(message=error or "方向参数无效")
    direction = direction_store.get_direction(conn, direction_id)
    assert direction is not None
    return _direction_response(direction)


@router.patch("/{direction_id}", response_model=Direction)
def update_direction(direction_id: int, payload: DirectionUpdate) -> Direction:
    conn = get_db()
    direction, error = direction_store.update_direction(
        conn,
        direction_id,
        label=payload.label,
        brief=payload.brief,
        enabled=payload.enabled,
    )
    if direction is None:
        raise NotFoundError(message=error or "方向不存在") if error == "方向不存在" else BadRequestError(message=error or "方向参数无效")
    return _direction_response(direction)


@router.delete("/{direction_id}", status_code=204)
def delete_direction(direction_id: int) -> None:
    conn = get_db()
    if not direction_store.delete_direction(conn, direction_id):
        raise NotFoundError(message="方向不存在")


@router.get("/{direction_id}", response_model=Direction)
def get_direction(direction_id: int) -> Direction:
    return _direction_response(_get_direction_or_404(direction_id))


@router.get("/{direction_id}/research")
def get_research(direction_id: int) -> dict[str, Any]:
    conn = get_db()
    direction_store.recover_stale_running(conn)
    direction = _get_direction_or_404(direction_id)
    summary = direction_store.research_summary(conn, direction)
    if summary is None:
        raise NotFoundError(message="该方向还没有研究版本")
    summary["direction_id"] = direction_id
    summary["prompt_id"] = DIRECTION_PROMPT_ID
    return summary


@router.post("/{direction_id}/research", response_model=DirectionResearchAction, status_code=202)
def start_research(direction_id: int) -> DirectionResearchAction:
    conn = get_db()
    direction = _get_direction_or_404(direction_id)
    running, error = direction_store.start_research(conn, direction_id)
    if running is None:
        raise ConflictError(message=error or "该方向已有研究在运行")
    _ensure_runner().submit(
        ResearchRequest(
            direction_id=direction_id,
            research_version=int(running["research_version"]),
            label=str(running["label_snapshot"]),
            brief=str(running["brief_snapshot"]),
        )
    )
    return DirectionResearchAction(
        direction_id=direction_id,
        research_version=int(running["research_version"]),
        status=STATUS_RUNNING,
    )
