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
from moni.scoring.bv_pipeline import short_llm_error
from moni.scoring.direction import (
    STATUS_RUNNING,
    build_draft_prompt,
    idea_reject_reason,
    parse_draft,
)
from moni.scoring.direction_runner import (
    ClarificationRequest,
    DirectionClarificationRunner,
    DirectionResearchRunner,
    ResearchRequest,
)
from moni.web.exceptions import BadRequestError, ConflictError, NotFoundError
from moni.web.models import (
    Direction,
    DirectionClarification,
    DirectionClarificationAction,
    DirectionClarificationAnswers,
    DirectionCreate,
    DirectionDraft,
    DirectionDraftRequest,
    DirectionResearchAction,
    DirectionResearchSummary,
    DirectionUpdate,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/directions", tags=["directions"])

_runner: DirectionResearchRunner | None = None
_clarification_runner: DirectionClarificationRunner | None = None
_runner_lock = threading.Lock()
#: Model identity captured at call time, so a settings change during the
#: call cannot mislabel which model produced the research.
_call_state = threading.local()


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
    research = direction_store.research_summary(conn, direction)
    usable, usable_error = direction_store.direction_usable(conn, direction)
    clarification = direction_store.clarification_summary(conn, direction)
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
        research_guidance=str(direction.get("research_guidance") or ""),
        reader_perspective=str(direction.get("reader_perspective") or ""),
        enabled=bool(direction["enabled"]),
        created_at=str(direction["created_at"]),
        updated_at=str(direction["updated_at"]),
        research=_research_action(research_row),
        clarification=(
            DirectionClarification(
                direction_id=int(direction["id"]),
                clarification_version=int(clarification["clarification_version"]),
                status=str(clarification["status"]),
                questions=list(clarification["questions"]),
                answers=list(clarification["answers"]),
                error=clarification["error"],
                started_at=clarification["started_at"],
                finished_at=clarification["finished_at"],
            )
            if clarification is not None
            else None
        ),
        usable=usable is not None,
        usable_error=usable_error,
    )


def _get_direction_or_404(direction_id: int) -> dict[str, Any]:
    conn = get_db()
    direction = direction_store.get_direction(conn, direction_id)
    if direction is None:
        raise NotFoundError(message="方向不存在")
    return direction


def _resolve_model() -> tuple[str, str, str]:
    """Return (provider, model, label) for the model that is active now."""
    from moni.llm.client import resolve_active_model
    from moni.llm.runner import read_model_mode

    if read_model_mode() == "profile":
        return "", "", "profile"
    provider, model = resolve_active_model()
    return provider, model, f"{provider}/{model}"


def _model_call(prompt: str) -> str:
    from moni.llm.client import call_llm

    provider, model, label = _resolve_model()
    _call_state.model_label = label
    return call_llm(provider, model, prompt)


def _model_name() -> str:
    captured = getattr(_call_state, "model_label", None)
    if captured:
        return captured
    return _resolve_model()[2]


def _ensure_runner() -> DirectionResearchRunner:
    global _runner
    with _runner_lock:
        if _runner is None:
            _runner = DirectionResearchRunner(_model_call, _model_name)
        return _runner


def _ensure_clarification_runner() -> DirectionClarificationRunner:
    global _clarification_runner
    with _runner_lock:
        if _clarification_runner is None:
            _clarification_runner = DirectionClarificationRunner(_model_call)
        return _clarification_runner


@router.get("", response_model=list[Direction])
def list_directions() -> list[Direction]:
    conn = get_db()
    direction_store.recover_stale_running(conn)
    direction_store.recover_stale_clarification(conn)
    return [_direction_response(direction) for direction in direction_store.list_directions(conn)]


@router.post("/draft", response_model=DirectionDraft)
def create_draft(payload: DirectionDraftRequest) -> DirectionDraft:
    """Turn a fragment of an idea into a full direction plus guidance.

    This is the first half of the two-stage flow: the user says roughly
    what they want, the model writes it down properly, and the user edits
    the result before any research runs.  Nothing is saved here -- the
    draft is returned to the form, and only the user's save persists it.
    """
    error = idea_reject_reason(payload.idea)
    if error:
        raise BadRequestError(message=error)
    try:
        raw = _model_call(build_draft_prompt(payload.idea, payload.label))
    except Exception as exc:  # noqa: BLE001 - reported to the user as text
        log.warning("direction draft failed: %s", exc)
        raise BadRequestError(message=f"生成草稿失败：{short_llm_error(str(exc))}")
    draft, parse_error = parse_draft(raw)
    if draft is None:
        raise BadRequestError(message=f"草稿不可用：{parse_error or '模型输出无法解析'}")
    return DirectionDraft(
        label=draft.label,
        brief=draft.brief,
        research_guidance=draft.research_guidance,
    )


@router.post("", response_model=Direction, status_code=201)
def create_direction(payload: DirectionCreate) -> Direction:
    conn = get_db()
    direction_id, error = direction_store.create_direction(
        conn,
        payload.label,
        payload.brief,
        payload.research_guidance,
        payload.reader_perspective,
    )
    if direction_id is None:
        if error == direction_store.DUPLICATE_LABEL_ERROR:
            raise ConflictError(message=error)
        raise BadRequestError(message=error or "方向参数无效")
    direction = direction_store.get_direction(conn, direction_id)
    assert direction is not None
    return _direction_response(direction)


@router.patch("/{direction_id}", response_model=Direction)
def update_direction(direction_id: int, payload: DirectionUpdate) -> Direction:
    conn = get_db()
    before = direction_store.get_direction(conn, direction_id)
    direction, error = direction_store.update_direction(
        conn,
        direction_id,
        label=payload.label,
        brief=payload.brief,
        research_guidance=payload.research_guidance,
        reader_perspective=payload.reader_perspective,
        enabled=payload.enabled,
    )
    if direction is None:
        if error == "方向不存在":
            raise NotFoundError(message=error)
        if error == direction_store.DUPLICATE_LABEL_ERROR:
            raise ConflictError(message=error)
        raise BadRequestError(message=error or "方向参数无效")
    response = _direction_response(direction)
    if before is not None:
        # The caller needs to know whether the save killed the passed
        # research, otherwise "保存成功" hides the fact that the direction
        # just became unusable until someone runs research again.
        changed = (
            str(before.get("label") or "") != direction["label"]
            or str(before.get("brief") or "") != direction["brief"]
            or str(before.get("research_guidance") or "")
            != str(direction.get("research_guidance") or "")
            or str(before.get("reader_perspective") or "")
            != str(direction.get("reader_perspective") or "")
        )
        # A direction that never had a research has nothing to invalidate.
        # Saying "研究需重新生成" for it would be wrong -- there was no
        # research to regenerate in the first place.
        had_research = direction_store.research_summary(conn, before) is not None
        response.research_invalidated = changed and had_research and not response.usable
    return response


@router.delete("/{direction_id}", status_code=204)
def delete_direction(direction_id: int) -> None:
    conn = get_db()
    if not direction_store.delete_direction(conn, direction_id):
        raise NotFoundError(message="方向不存在")


@router.get("/{direction_id}", response_model=Direction)
def get_direction(direction_id: int) -> Direction:
    return _direction_response(_get_direction_or_404(direction_id))


@router.get("/{direction_id}/research")
def get_research(direction_id: int) -> DirectionResearchSummary:
    conn = get_db()
    direction_store.recover_stale_running(conn)
    direction = _get_direction_or_404(direction_id)
    summary = direction_store.research_summary(conn, direction)
    if summary is None:
        raise NotFoundError(message="该方向还没有研究版本")
    return DirectionResearchSummary(direction_id=direction_id, **summary)


@router.post("/{direction_id}/research", response_model=DirectionResearchAction, status_code=202)
def start_research(direction_id: int) -> DirectionResearchAction:
    conn = get_db()
    direction = _get_direction_or_404(direction_id)
    if not direction["enabled"]:
        raise ConflictError(message="方向已停用，请先启用再研究")
    running, error = direction_store.start_research(conn, direction_id)
    if running is None:
        raise ConflictError(message=error or "该方向已有研究在运行")
    _ensure_runner().submit(
        ResearchRequest(
            direction_id=direction_id,
            research_version=int(running["research_version"]),
            label=str(running["label_snapshot"]),
            brief=str(running["brief_snapshot"]),
            research_guidance=str(running.get("guidance_snapshot") or ""),
            reader_perspective=str(running.get("perspective_snapshot") or ""),
            clarification_answers=str(running.get("clarification_snapshot") or ""),
        )
    )
    return DirectionResearchAction(
        direction_id=direction_id,
        research_version=int(running["research_version"]),
        status=STATUS_RUNNING,
    )


def _clarification_response(
    direction_id: int, summary: dict[str, Any]
) -> DirectionClarification:
    return DirectionClarification(
        direction_id=direction_id,
        clarification_version=int(summary["clarification_version"]),
        status=str(summary["status"]),
        questions=list(summary["questions"]),
        answers=list(summary["answers"]),
        error=summary["error"],
        started_at=summary["started_at"],
        finished_at=summary["finished_at"],
    )


@router.get("/{direction_id}/clarification", response_model=DirectionClarification)
def get_clarification(direction_id: int) -> DirectionClarification:
    conn = get_db()
    direction_store.recover_stale_clarification(conn)
    direction = _get_direction_or_404(direction_id)
    summary = direction_store.clarification_summary(conn, direction)
    if summary is None:
        raise NotFoundError(message="该方向还没有澄清记录")
    return _clarification_response(direction_id, summary)


@router.post(
    "/{direction_id}/clarification",
    response_model=DirectionClarificationAction,
    status_code=202,
)
def start_clarification(direction_id: int) -> DirectionClarificationAction:
    """Ask the model what it still needs to know about the reader perspective."""
    conn = get_db()
    direction = _get_direction_or_404(direction_id)
    if not direction["enabled"]:
        raise ConflictError(message="方向已停用，请先启用再澄清")
    running, error = direction_store.start_clarification(conn, direction_id)
    if running is None:
        raise ConflictError(message=error or "该方向已有澄清在运行")
    _ensure_clarification_runner().submit(
        ClarificationRequest(
            direction_id=direction_id,
            clarification_version=int(running["clarification_version"]),
            label=str(direction["label"]),
            brief=str(direction["brief"]),
            reader_perspective=str(running["perspective_snapshot"]),
        )
    )
    return DirectionClarificationAction(
        direction_id=direction_id,
        clarification_version=int(running["clarification_version"]),
        status=STATUS_RUNNING,
    )


@router.put(
    "/{direction_id}/clarification/answers", response_model=DirectionClarification
)
def save_clarification_answers(
    direction_id: int, payload: DirectionClarificationAnswers
) -> DirectionClarification:
    """Store the user's answers; a changed answer expires the research."""
    conn = get_db()
    _get_direction_or_404(direction_id)
    saved, error = direction_store.save_clarification_answers(
        conn, direction_id, payload.answers
    )
    if saved is None:
        raise BadRequestError(message=error or "澄清回答无效")
    direction = _get_direction_or_404(direction_id)
    summary = direction_store.clarification_summary(conn, direction)
    assert summary is not None
    return _clarification_response(direction_id, summary)
