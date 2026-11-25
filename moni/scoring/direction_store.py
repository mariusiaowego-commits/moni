"""Persistence and the research state machine for business directions.

This is the only module that writes ``bv_direction*`` rows.  The state
machine is deliberately unforgiving: a failed or running latest version
makes the direction unusable, and the only way forward is a new
research run.  Older rows stay as an audit trail.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Mapping

from .direction import (
    BRIEF_MAX_CHARS,
    CLARIFY_ANSWER_MAX_CHARS,
    CLARIFY_PROMPT_ID,
    GUIDANCE_MAX_CHARS,
    READER_PERSPECTIVE_MAX_CHARS,
    STATUS_EXPIRED,
    STATUS_FAILED,
    STATUS_PASSED,
    STATUS_RUNNING,
    brief_reject_reason,
    clarification_text,
    guidance_reject_reason,
    label_reject_reason,
    parse_research,
    reader_reject_reason,
    research_prompt_id,
    usable_research,
)

#: A research run is a single Hermes call; five minutes is generous,
#: and anything still running after ten minutes is treated as dead.
RESEARCH_STALE_AFTER_SEC = 600
#: Returned by create/update when another direction already owns the name.
DUPLICATE_LABEL_ERROR = "方向名已存在"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _reject_reason(
    label: str,
    brief: str,
    research_guidance: str = "",
    reader_perspective: str = "",
) -> str | None:
    label_error = label_reject_reason(label)
    if label_error:
        return label_error
    brief_error = brief_reject_reason(brief)
    if brief_error:
        return brief_error
    if len((brief or "").strip()) > BRIEF_MAX_CHARS:
        return f"方向说明超过 {BRIEF_MAX_CHARS} 字"
    guidance_error = guidance_reject_reason(research_guidance)
    if guidance_error:
        return guidance_error
    perspective_error = reader_reject_reason(reader_perspective)
    if perspective_error:
        return perspective_error
    return None


def _label_taken(
    conn: sqlite3.Connection, label: str, exclude_id: int | None = None
) -> bool:
    if exclude_id is None:
        row = conn.execute(
            "SELECT 1 FROM bv_direction WHERE label=? LIMIT 1", (label,)
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT 1 FROM bv_direction WHERE label=? AND id<>? LIMIT 1",
            (label, exclude_id),
        ).fetchone()
    return row is not None


def create_direction(
    conn: sqlite3.Connection,
    label: str,
    brief: str,
    research_guidance: str = "",
    reader_perspective: str = "",
) -> tuple[int | None, str | None]:
    error = _reject_reason(label, brief, research_guidance, reader_perspective)
    if error:
        return None, error
    now = _now()
    clean_label = label.strip()
    conn.execute("BEGIN IMMEDIATE")
    try:
        if _label_taken(conn, clean_label):
            conn.rollback()
            return None, DUPLICATE_LABEL_ERROR
        cur = conn.execute(
            "INSERT INTO bv_direction (label, brief, research_guidance, "
            "reader_perspective, enabled, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, 1, ?, ?)",
            (
                clean_label,
                brief.strip(),
                (research_guidance or "").strip(),
                (reader_perspective or "").strip(),
                now,
                now,
            ),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return int(cur.lastrowid), None


def update_direction(
    conn: sqlite3.Connection,
    direction_id: int,
    *,
    label: str | None = None,
    brief: str | None = None,
    research_guidance: str | None = None,
    reader_perspective: str | None = None,
    enabled: bool | None = None,
) -> tuple[dict[str, Any] | None, str | None]:
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT label, brief, research_guidance, reader_perspective, enabled "
            "FROM bv_direction WHERE id=?",
            (direction_id,),
        ).fetchone()
        if row is None:
            conn.rollback()
            return None, "方向不存在"

        next_label = row["label"] if label is None else label.strip()
        next_brief = row["brief"] if brief is None else brief.strip()
        next_guidance = (
            (row["research_guidance"] or "")
            if research_guidance is None
            else research_guidance.strip()
        )
        next_perspective = (
            (row["reader_perspective"] or "")
            if reader_perspective is None
            else reader_perspective.strip()
        )
        next_enabled = bool(row["enabled"]) if enabled is None else bool(enabled)
        research_invalidated = (
            next_label != row["label"]
            or next_brief != row["brief"]
            or next_guidance != (row["research_guidance"] or "").strip()
            or next_perspective != (row["reader_perspective"] or "").strip()
        )
        if research_invalidated:
            error = _reject_reason(
                next_label, next_brief, next_guidance, next_perspective
            )
            if error:
                conn.rollback()
                return None, error
        if _label_taken(conn, next_label, exclude_id=direction_id):
            conn.rollback()
            return None, DUPLICATE_LABEL_ERROR

        conn.execute(
            "UPDATE bv_direction SET label=?, brief=?, research_guidance=?, "
            "reader_perspective=?, enabled=?, updated_at=? WHERE id=?",
            (
                next_label,
                next_brief,
                next_guidance,
                next_perspective,
                1 if next_enabled else 0,
                now,
                direction_id,
            ),
        )
        # A research snapshot carries the label, brief and guidance it was
        # built from.  Only a real change invalidates it; toggling
        # ``enabled`` alone must keep a passed research result usable.
        if research_invalidated:
            conn.execute(
                "UPDATE bv_direction_research SET status=? "
                "WHERE direction_id=? AND status=?",
                (STATUS_EXPIRED, direction_id, STATUS_PASSED),
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return get_direction(conn, direction_id), None


def get_direction(conn: sqlite3.Connection, direction_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT id, label, brief, research_guidance, reader_perspective, enabled, "
        "created_at, updated_at "
        "FROM bv_direction WHERE id=?",
        (direction_id,),
    ).fetchone()
    return dict(row) if row is not None else None


def list_directions(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT id, label, brief, research_guidance, reader_perspective, enabled, "
        "created_at, updated_at "
        "FROM bv_direction ORDER BY id"
    ).fetchall()
    return [dict(row) for row in rows]


def delete_direction(conn: sqlite3.Connection, direction_id: int) -> bool:
    row = get_direction(conn, direction_id)
    if row is None:
        return False
    conn.execute("DELETE FROM bv_direction WHERE id=?", (direction_id,))
    conn.commit()
    return True


def latest_research_rows(
    conn: sqlite3.Connection, direction_id: int
) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT id, direction_id, research_version, status, label_snapshot, "
        "brief_snapshot, guidance_snapshot, perspective_snapshot, "
        "clarification_snapshot, prompt_id, scope, out_of_scope, "
        "rubric_json, error, model, started_at, finished_at "
        "FROM bv_direction_research WHERE direction_id=? "
        "ORDER BY research_version DESC",
        (direction_id,),
    ).fetchall()


def recover_stale_running(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> int:
    """Fail research rows left running by a crashed process.

    Called by the routes that read status, so the UI never waits forever
    on a dead worker.  ``start_research`` uses the transaction-internal
    helper instead.
    """
    now = now or datetime.now(timezone.utc)
    dead = _stale_running_rows(conn, now)
    if not dead:
        return 0
    conn.execute("BEGIN IMMEDIATE")
    try:
        _recover_stale_running_locked(conn, now=now, dead=dead)
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return len(dead)


def _stale_running_rows(
    conn: sqlite3.Connection, now: datetime
) -> list[tuple[int, str]]:
    cutoff = now.timestamp() - RESEARCH_STALE_AFTER_SEC
    dead: list[tuple[int, str]] = []
    for row in conn.execute(
        "SELECT id, started_at FROM bv_direction_research WHERE status=?",
        (STATUS_RUNNING,),
    ).fetchall():
        try:
            started_at = datetime.fromisoformat(row["started_at"])
        except (TypeError, ValueError):
            dead.append((int(row["id"]), "研究开始时间无法解析"))
            continue
        # SQLite's datetime('now') writes naive UTC; treat those as UTC so
        # a local-timezone machine does not read them as eight hours old.
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if started_at.timestamp() < cutoff:
            dead.append((int(row["id"]), "研究中断或超时"))
    return dead


def _recover_stale_running_locked(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    dead: list[tuple[int, str]] | None = None,
) -> None:
    """Caller owns the transaction; only the writes happen here."""
    rows = _stale_running_rows(conn, now) if dead is None else dead
    finished_at = now.astimezone(timezone.utc).isoformat()
    for research_id, reason in rows:
        conn.execute(
            "UPDATE bv_direction_research SET status=?, error=?, finished_at=? "
            "WHERE id=? AND status=?",
            (STATUS_FAILED, reason, finished_at, research_id, STATUS_RUNNING),
        )


def start_research(
    conn: sqlite3.Connection, direction_id: int
) -> tuple[dict[str, Any] | None, str | None]:
    """Create the next research version and mark older ones expired."""
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        direction = conn.execute(
            "SELECT label, brief, research_guidance, reader_perspective "
            "FROM bv_direction WHERE id=?",
            (direction_id,),
        ).fetchone()
        if direction is None:
            conn.rollback()
            return None, "方向不存在"

        _recover_stale_running_locked(conn, now=datetime.fromisoformat(now))
        active = conn.execute(
            "SELECT COUNT(*) FROM bv_direction_research "
            "WHERE direction_id=? AND status=?",
            (direction_id, STATUS_RUNNING),
        ).fetchone()[0]
        if active:
            conn.rollback()
            return None, "该方向已有研究在运行"

        perspective = (direction["reader_perspective"] or "").strip()
        clarification = _current_clarification(conn, direction_id, perspective)
        if perspective:
            # A one-line perspective is not enough to rewrite ten scoring
            # questions, so the direction has to be clarified first.
            if clarification is None:
                conn.rollback()
                return None, "读者视角还没澄清过，请先生成澄清问题"
            unanswered = sum(
                1 for answer in clarification["answers"] if not (answer or "").strip()
            )
            if unanswered:
                conn.rollback()
                return None, f"还有 {unanswered} 个澄清问题没回答"

        next_version = int(
            conn.execute(
                "SELECT COALESCE(MAX(research_version), 0) + 1 "
                "FROM bv_direction_research WHERE direction_id=?",
                (direction_id,),
            ).fetchone()[0]
        )
        conn.execute(
            "UPDATE bv_direction_research SET status=? "
            "WHERE direction_id=? AND status=?",
            (STATUS_EXPIRED, direction_id, STATUS_PASSED),
        )
        cur = conn.execute(
            "INSERT INTO bv_direction_research "
            "(direction_id, research_version, status, label_snapshot, "
            "brief_snapshot, guidance_snapshot, perspective_snapshot, "
            "clarification_snapshot, prompt_id, started_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                direction_id,
                next_version,
                STATUS_RUNNING,
                direction["label"],
                direction["brief"],
                (direction["research_guidance"] or "").strip(),
                perspective,
                clarification["text"] if clarification else "",
                research_prompt_id(perspective),
                now,
            ),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    research_id = int(cur.lastrowid)
    return {
        "direction_id": direction_id,
        "research_id": research_id,
        "research_version": next_version,
        "status": STATUS_RUNNING,
        "label_snapshot": direction["label"],
        "brief_snapshot": direction["brief"],
        "guidance_snapshot": (direction["research_guidance"] or "").strip(),
        "perspective_snapshot": perspective,
        "clarification_snapshot": clarification["text"] if clarification else "",
        "prompt_id": research_prompt_id(perspective),
        "started_at": now,
    }, None


def finish_research_success(
    conn: sqlite3.Connection,
    *,
    direction_id: int,
    research_version: int,
    raw_research: str | Mapping[str, Any],
    model: str,
) -> tuple[bool, str | None]:
    research = parse_research(raw_research)
    parsed, parse_error = research
    if parsed is None:
        finish_research_failure(
            conn,
            direction_id=direction_id,
            research_version=research_version,
            error=parse_error or "研究方向校验未通过",
        )
        return False, parse_error

    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "UPDATE bv_direction_research SET status=?, scope=?, out_of_scope=?, "
            "rubric_json=?, model=?, finished_at=? "
            "WHERE direction_id=? AND research_version=? AND status=?",
            (
                STATUS_PASSED,
                parsed.scope,
                parsed.out_of_scope,
                json.dumps(parsed.to_payload(), ensure_ascii=False),
                model,
                _now(),
                direction_id,
                research_version,
                STATUS_RUNNING,
            ),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return False, "研究已结束或已超时"
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return True, None


def finish_research_failure(
    conn: sqlite3.Connection,
    *,
    direction_id: int,
    research_version: int,
    error: str,
) -> None:
    conn.execute(
        "UPDATE bv_direction_research SET status=?, error=?, finished_at=? "
        "WHERE direction_id=? AND research_version=? AND status=?",
        (
            STATUS_FAILED,
            str(error)[:500],
            _now(),
            direction_id,
            research_version,
            STATUS_RUNNING,
        ),
    )
    conn.commit()


def direction_usable(
    conn: sqlite3.Connection, direction: Mapping[str, Any]
) -> tuple[Any | None, str | None]:
    rows = latest_research_rows(conn, int(direction["id"]))
    perspective = str(direction.get("reader_perspective") or "").strip()
    clarification = _current_clarification(conn, int(direction["id"]), perspective)
    return usable_research(
        rows,
        label=str(direction["label"]),
        brief=str(direction["brief"]),
        research_guidance=str(direction.get("research_guidance") or ""),
        reader_perspective=perspective,
        clarification_snapshot=clarification["text"] if clarification else "",
        direction_id=int(direction["id"]),
    )


def _current_clarification(
    conn: sqlite3.Connection, direction_id: int, perspective: str
) -> dict[str, Any] | None:
    """The latest passed clarification round for this perspective.

    A round that was made for an older perspective does not count: the
    user changed the perspective, so the old answers describe something
    else.
    """
    row = conn.execute(
        "SELECT id, clarification_version, questions_json, answers_json "
        "FROM bv_direction_clarification "
        "WHERE direction_id=? AND status=? AND perspective_snapshot=? "
        "ORDER BY clarification_version DESC LIMIT 1",
        (direction_id, STATUS_PASSED, perspective),
    ).fetchone()
    if row is None:
        return None
    questions = _json_list(row["questions_json"])
    answers = _json_list(row["answers_json"])
    return {
        "clarification_id": int(row["id"]),
        "clarification_version": int(row["clarification_version"]),
        "questions": questions,
        "answers": answers,
        "text": clarification_text(questions, answers),
    }


def _json_list(raw: Any) -> list[str]:
    try:
        data = json.loads(raw or "[]")
    except (TypeError, ValueError):
        return []
    if not isinstance(data, list):
        return []
    return [item if isinstance(item, str) else "" for item in data]


def start_clarification(
    conn: sqlite3.Connection, direction_id: int
) -> tuple[dict[str, Any] | None, str | None]:
    """Open a clarification round for the direction's reader perspective."""
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        direction = conn.execute(
            "SELECT label, brief, reader_perspective FROM bv_direction WHERE id=?",
            (direction_id,),
        ).fetchone()
        if direction is None:
            conn.rollback()
            return None, "方向不存在"
        perspective = (direction["reader_perspective"] or "").strip()
        if not perspective:
            conn.rollback()
            return None, "这个方向没有读者视角，不需要澄清"
        active = conn.execute(
            "SELECT COUNT(*) FROM bv_direction_clarification "
            "WHERE direction_id=? AND status=?",
            (direction_id, STATUS_RUNNING),
        ).fetchone()[0]
        if active:
            conn.rollback()
            return None, "该方向已有澄清在运行"
        next_version = int(
            conn.execute(
                "SELECT COALESCE(MAX(clarification_version), 0) + 1 "
                "FROM bv_direction_clarification WHERE direction_id=?",
                (direction_id,),
            ).fetchone()[0]
        )
        cur = conn.execute(
            "INSERT INTO bv_direction_clarification "
            "(direction_id, clarification_version, status, perspective_snapshot, "
            "questions_json, answers_json, prompt_id, started_at) "
            "VALUES (?, ?, ?, ?, '[]', '[]', ?, ?)",
            (
                direction_id,
                next_version,
                STATUS_RUNNING,
                perspective,
                CLARIFY_PROMPT_ID,
                now,
            ),
        )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return {
        "direction_id": direction_id,
        "clarification_id": int(cur.lastrowid),
        "clarification_version": next_version,
        "status": STATUS_RUNNING,
        "perspective_snapshot": perspective,
        "questions": [],
        "answers": [],
        "prompt_id": CLARIFY_PROMPT_ID,
        "started_at": now,
    }, None


def finish_clarification_success(
    conn: sqlite3.Connection,
    *,
    direction_id: int,
    clarification_version: int,
    raw_clarification: str | Mapping[str, Any],
) -> tuple[bool, str | None]:
    from .direction import parse_clarification

    parsed, parse_error = parse_clarification(raw_clarification)
    if parsed is None:
        finish_clarification_failure(
            conn,
            direction_id=direction_id,
            clarification_version=clarification_version,
            error=parse_error or "澄清结果校验未通过",
        )
        return False, parse_error
    conn.execute("BEGIN IMMEDIATE")
    try:
        cur = conn.execute(
            "UPDATE bv_direction_clarification SET status=?, questions_json=?, "
            "answers_json=?, raw_output=?, finished_at=? "
            "WHERE direction_id=? AND clarification_version=? AND status=?",
            (
                STATUS_PASSED,
                json.dumps(list(parsed.questions), ensure_ascii=False),
                json.dumps([""] * len(parsed.questions), ensure_ascii=False),
                json.dumps(raw_clarification, ensure_ascii=False)
                if isinstance(raw_clarification, Mapping)
                else str(raw_clarification),
                _now(),
                direction_id,
                clarification_version,
                STATUS_RUNNING,
            ),
        )
        if cur.rowcount == 0:
            conn.rollback()
            return False, "澄清已结束或已超时"
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return True, None


def finish_clarification_failure(
    conn: sqlite3.Connection,
    *,
    direction_id: int,
    clarification_version: int,
    error: str,
) -> None:
    conn.execute(
        "UPDATE bv_direction_clarification SET status=?, error=?, finished_at=? "
        "WHERE direction_id=? AND clarification_version=? AND status=?",
        (
            STATUS_FAILED,
            str(error)[:500],
            _now(),
            direction_id,
            clarification_version,
            STATUS_RUNNING,
        ),
    )
    conn.commit()


def save_clarification_answers(
    conn: sqlite3.Connection,
    direction_id: int,
    answers: list[str],
) -> tuple[dict[str, Any] | None, str | None]:
    """Store the user's answers and expire a research built without them."""
    now = _now()
    conn.execute("BEGIN IMMEDIATE")
    try:
        direction = conn.execute(
            "SELECT reader_perspective FROM bv_direction WHERE id=?",
            (direction_id,),
        ).fetchone()
        if direction is None:
            conn.rollback()
            return None, "方向不存在"
        perspective = (direction["reader_perspective"] or "").strip()
        row = conn.execute(
            "SELECT id, clarification_version, questions_json, answers_json "
            "FROM bv_direction_clarification "
            "WHERE direction_id=? AND status=? AND perspective_snapshot=? "
            "ORDER BY clarification_version DESC LIMIT 1",
            (direction_id, STATUS_PASSED, perspective),
        ).fetchone()
        if row is None:
            conn.rollback()
            return None, "还没有通过的澄清，无法保存回答"
        questions = _json_list(row["questions_json"])
        if len(answers) != len(questions):
            conn.rollback()
            return None, f"回答条数应为 {len(questions)} 条"
        for answer in answers:
            if len((answer or "").strip()) > CLARIFY_ANSWER_MAX_CHARS:
                conn.rollback()
                return None, f"回答超过 {CLARIFY_ANSWER_MAX_CHARS} 字"
        clean = [(answer or "").strip() for answer in answers]
        previous = _json_list(row["answers_json"])
        conn.execute(
            "UPDATE bv_direction_clarification SET answers_json=? WHERE id=?",
            (json.dumps(clean, ensure_ascii=False), int(row["id"])),
        )
        if clean != previous:
            # The answers are part of the research input, so a changed
            # answer expires the research that was built from the old one.
            conn.execute(
                "UPDATE bv_direction_research SET status=? "
                "WHERE direction_id=? AND status=?",
                (STATUS_EXPIRED, direction_id, STATUS_PASSED),
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return {
        "direction_id": direction_id,
        "clarification_version": int(row["clarification_version"]),
        "questions": questions,
        "answers": clean,
    }, None


def clarification_summary(
    conn: sqlite3.Connection, direction: Mapping[str, Any]
) -> dict[str, Any] | None:
    """The latest clarification round of any status, for the UI."""
    row = conn.execute(
        "SELECT id, clarification_version, status, perspective_snapshot, "
        "questions_json, answers_json, error, started_at, finished_at "
        "FROM bv_direction_clarification WHERE direction_id=? "
        "ORDER BY clarification_version DESC LIMIT 1",
        (int(direction["id"]),),
    ).fetchone()
    if row is None:
        return None
    return {
        "clarification_id": int(row["id"]),
        "clarification_version": int(row["clarification_version"]),
        "status": str(row["status"]),
        "perspective_snapshot": str(row["perspective_snapshot"] or ""),
        "questions": _json_list(row["questions_json"]),
        "answers": _json_list(row["answers_json"]),
        "error": row["error"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


def recover_stale_clarification(
    conn: sqlite3.Connection, *, now: datetime | None = None
) -> int:
    """Fail clarification rows left running by a crashed process."""
    now = now or datetime.now(timezone.utc)
    cutoff = now.timestamp() - RESEARCH_STALE_AFTER_SEC
    dead: list[int] = []
    for row in conn.execute(
        "SELECT id, started_at FROM bv_direction_clarification WHERE status=?",
        (STATUS_RUNNING,),
    ).fetchall():
        try:
            started_at = datetime.fromisoformat(row["started_at"])
        except (TypeError, ValueError):
            dead.append(int(row["id"]))
            continue
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        if started_at.timestamp() < cutoff:
            dead.append(int(row["id"]))
    if not dead:
        return 0
    finished_at = now.astimezone(timezone.utc).isoformat()
    conn.execute("BEGIN IMMEDIATE")
    try:
        for clarification_id in dead:
            conn.execute(
                "UPDATE bv_direction_clarification SET status=?, error=?, "
                "finished_at=? WHERE id=? AND status=?",
                (
                    STATUS_FAILED,
                    "澄清中断或超时",
                    finished_at,
                    clarification_id,
                    STATUS_RUNNING,
                ),
            )
        conn.commit()
    except sqlite3.Error:
        conn.rollback()
        raise
    return len(dead)


def research_summary(
    conn: sqlite3.Connection, direction: Mapping[str, Any]
) -> dict[str, Any] | None:
    rows = latest_research_rows(conn, int(direction["id"]))
    if not rows:
        return None
    row = rows[0]
    return {
        "research_id": int(row["id"]),
        "research_version": int(row["research_version"]),
        "status": str(row["status"]),
        "scope": row["scope"],
        "out_of_scope": row["out_of_scope"],
        "error": row["error"],
        "prompt_id": row["prompt_id"],
        "model": row["model"],
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
    }


__all__ = [
    "BRIEF_MAX_CHARS",
    "CLARIFY_ANSWER_MAX_CHARS",
    "GUIDANCE_MAX_CHARS",
    "READER_PERSPECTIVE_MAX_CHARS",
    "RESEARCH_STALE_AFTER_SEC",
    "clarification_summary",
    "create_direction",
    "delete_direction",
    "direction_usable",
    "finish_clarification_failure",
    "finish_clarification_success",
    "finish_research_failure",
    "finish_research_success",
    "get_direction",
    "latest_research_rows",
    "list_directions",
    "recover_stale_clarification",
    "recover_stale_running",
    "research_summary",
    "save_clarification_answers",
    "start_clarification",
    "start_research",
    "update_direction",
]
