"""Direction-specific business-value scores (Sprint 67, S3).

The general ten-dimension score lives on ``articles.business_value_*``
(0-100 integer, computed by the same harness).  A direction run reuses
that harness with the direction's own question set and stores its row in
``article_bv_direction``, so the two scores never share a cache key.

State machine, one row per (article, direction, research version):

    queued -> running -> scored | needs_review | failed | insufficient

``queued`` is written by the API when the row enters the worker queue;
``running`` is written by the worker when it actually starts that article.
The two states are separate on purpose: a batch of twelve articles runs
one at a time, so a single watchdog on ``running`` would kill articles
that were merely waiting their turn.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

from . import direction_store
from .bv_pipeline import BvResult, content_hash, score_general_bv
from .direction import research_to_rubric
from .store import public_breakdown
from .snapshot import from_row

STATUS_QUEUED = "queued"
STATUS_RUNNING = "running"
STATUS_SCORED = "scored"
STATUS_NEEDS_REVIEW = "needs_review"
STATUS_FAILED = "failed"
STATUS_INSUFFICIENT = "insufficient"

#: Statuses that are not recomputed while the body stays unchanged.
SETTLED_STATUSES = (STATUS_SCORED, STATUS_NEEDS_REVIEW, STATUS_INSUFFICIENT)
#: Statuses that mean "a worker owns this row right now".
INFLIGHT_STATUSES = (STATUS_QUEUED, STATUS_RUNNING)

#: One POST may queue at most this many articles.  The cap is on work
#: actually started, not on the number of ids sent.
MAX_BATCH = 12
#: Protocol cap on ``article_ids`` length, so a huge selection cannot be
#: posted by accident.
MAX_REQUEST = 50
#: A row left ``running`` for longer than this is treated as dead.
STALE_RUNNING_AFTER_SEC = 600
#: Seconds of model time one article may burn.
#:
#: A long article plus the ten-dimension rubric makes a prompt of tens of
#: thousands of characters; the model needs well over 90 seconds for the
#: evidence pass on those, and a 90-second cap turned every one of them
#: into a bare "取证失败" in production.
DEFAULT_BUDGET_S = 300.0
#: Per-call timeout handed to the model.
DEFAULT_STEP_TIMEOUT = 180.0


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def resolve_direction(
    conn: sqlite3.Connection, direction_id: int
) -> tuple[dict[str, Any] | None, Any | None, str | None]:
    """Return ``(direction, research, reason)`` for a scorable direction.

    ``research`` is the latest *passed* research row; ``reason`` explains
    why a direction cannot be scored.  Only one of them is ever set.
    """
    row = conn.execute(
        "SELECT id, label, brief, reader_perspective, enabled FROM bv_direction WHERE id=?",
        (direction_id,),
    ).fetchone()
    if row is None:
        return None, None, "方向不存在"
    direction = dict(row)
    if not direction["enabled"]:
        return direction, None, "方向已停用，请先启用"
    research, reason = direction_store.direction_usable(conn, direction)
    if research is None:
        return direction, None, reason or "方向研究不可用"
    return direction, research, None


def resolve_rubric(
    conn: sqlite3.Connection, direction_id: int
) -> tuple[dict[str, Any] | None, Any | None, Any | None, str | None]:
    """Return ``(direction, research, rubric, reason)``.

    The rubric is the direction's own question set.  It is only built
    when the research passed validation, so a broken research version can
    never reach the scoring pipeline.
    """
    direction, research, reason = resolve_direction(conn, direction_id)
    if research is None:
        return direction, None, None, reason
    return direction, research, research_to_rubric(research), None


def _row_payload(row: sqlite3.Row | Mapping[str, Any]) -> dict[str, Any]:
    def get(key: str) -> Any:
        try:
            return row[key]
        except (KeyError, IndexError, TypeError):
            return None

    breakdown = None
    raw = get("breakdown_json")
    if raw:
        try:
            parsed = json.loads(raw)
            breakdown = public_breakdown(parsed)
        except (TypeError, ValueError):
            breakdown = None
    return {
        "score": get("score"),
        "note": get("note") or "",
        "status": get("status"),
        "error": get("error"),
        "breakdown": breakdown,
        "scored_at": get("scored_at"),
    }


def article_hashes(
    conn: sqlite3.Connection, article_ids: Iterable[str]
) -> dict[str, str]:
    """Body hash per article.  Missing articles are simply absent."""
    ids = [str(a) for a in article_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        f"SELECT id, raw_content FROM articles WHERE id IN ({placeholders})",
        tuple(ids),
    ).fetchall()
    return {str(r["id"]): content_hash(str(r["raw_content"] or "")) for r in rows}


def load_scores(
    conn: sqlite3.Connection,
    *,
    direction_id: int,
    research_version: int,
    article_ids: Iterable[str],
) -> dict[str, dict[str, Any]]:
    """Cached rows for this research version, flagged ``stale`` by body hash."""
    ids = [str(a) for a in article_ids]
    if not ids:
        return {}
    placeholders = ",".join("?" for _ in ids)
    rows = conn.execute(
        "SELECT article_id, score, note, status, error, breakdown_json, "
        "content_hash, scored_at FROM article_bv_direction "
        f"WHERE direction_id=? AND research_version=? AND article_id IN ({placeholders})",
        (direction_id, research_version, *ids),
    ).fetchall()
    if not rows:
        return {}
    bodies = article_hashes(conn, ids)
    out: dict[str, dict[str, Any]] = {}
    for row in rows:
        article_id = str(row["article_id"])
        payload = _row_payload(row)
        payload["stale"] = bodies.get(article_id) != row["content_hash"]
        out[article_id] = payload
    return out


def mark_queued(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    direction_id: int,
    research_version: int,
    body_hash: str,
    rubric_id: str,
) -> bool:
    """Put a row into the queue.  False when a worker already owns it."""
    now = _now()
    cur = conn.execute(
        "INSERT INTO article_bv_direction "
        "(article_id, direction_id, research_version, score, note, status, error, "
        " breakdown_json, content_hash, rubric_id, scored_at) "
        "VALUES (?, ?, ?, NULL, NULL, ?, NULL, NULL, ?, ?, ?) "
        "ON CONFLICT(article_id, direction_id, research_version) DO UPDATE SET "
        "status=excluded.status, score=NULL, note=NULL, error=NULL, "
        "breakdown_json=NULL, content_hash=excluded.content_hash, "
        "rubric_id=excluded.rubric_id, scored_at=excluded.scored_at "
        "WHERE article_bv_direction.status NOT IN (?, ?)",
        (
            article_id,
            direction_id,
            research_version,
            STATUS_QUEUED,
            body_hash,
            rubric_id,
            now,
            STATUS_QUEUED,
            STATUS_RUNNING,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def mark_running(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    direction_id: int,
    research_version: int,
) -> bool:
    """Claim a queued row for scoring.  False when it was taken already."""
    cur = conn.execute(
        "UPDATE article_bv_direction SET status=?, scored_at=? "
        "WHERE article_id=? AND direction_id=? AND research_version=? AND status=?",
        (
            STATUS_RUNNING,
            _now(),
            article_id,
            direction_id,
            research_version,
            STATUS_QUEUED,
        ),
    )
    conn.commit()
    return cur.rowcount > 0


def map_status(result: BvResult) -> str:
    """Map a harness result onto the stored status vocabulary."""
    if result.status == "insufficient":
        return STATUS_INSUFFICIENT
    if result.status == "ready":
        return STATUS_SCORED
    if result.status == "needs_review":
        return STATUS_NEEDS_REVIEW
    return STATUS_FAILED


def write_score(
    conn: sqlite3.Connection,
    *,
    article_id: str,
    direction_id: int,
    research_version: int,
    result: BvResult,
    rubric_id: str,
) -> dict[str, Any]:
    """Persist the terminal state.  Only a ``running`` row may be written."""
    breakdown = result.breakdown
    status = map_status(result)
    cur = conn.execute(
        "UPDATE article_bv_direction SET score=?, note=?, status=?, error=?, "
        "breakdown_json=?, content_hash=?, rubric_id=?, scored_at=? "
        "WHERE article_id=? AND direction_id=? AND research_version=? AND status=?",
        (
            result.total,
            result.note or None,
            status,
            result.error,
            json.dumps(breakdown, ensure_ascii=False) if breakdown else None,
            result.content_hash,
            rubric_id,
            _now(),
            article_id,
            direction_id,
            research_version,
            STATUS_RUNNING,
        ),
    )
    conn.commit()
    return {
        "score": result.total,
        "note": result.note or "",
        "status": status,
        "error": result.error,
        "breakdown": breakdown,
        "written": cur.rowcount > 0,
    }


def score_one(
    conn: sqlite3.Connection,
    *,
    direction_id: int,
    research_version: int,
    rubric: Any,
    article_row: Mapping[str, Any] | sqlite3.Row,
    llm: Any,
    budget_s: float = DEFAULT_BUDGET_S,
    step_timeout: float = DEFAULT_STEP_TIMEOUT,
) -> dict[str, Any]:
    """Score one article and persist the outcome.  Never raises."""
    snap = from_row(article_row)
    try:
        result = score_general_bv(
            snap, rubric=rubric, llm=llm, budget_s=budget_s, step_timeout=step_timeout,
        )
    except Exception as exc:  # noqa: BLE001 - persisted, never raised
        result = BvResult(
            status="failed",
            error=str(exc),
            content_hash=content_hash(snap.body),
        )
    return write_score(
        conn,
        article_id=snap.id,
        direction_id=direction_id,
        research_version=research_version,
        result=result,
        rubric_id=rubric.id,
    )


def recover_stale(
    conn: sqlite3.Connection,
    *,
    live_keys: Iterable[tuple[str, int, int]] = (),
    now: datetime | None = None,
) -> int:
    """Fail rows that no live worker can finish.

    Two kinds are dead: a ``running`` row older than the watchdog, and a
    ``queued`` row whose key is not in the live queue -- the leftover of
    a process restart.
    """
    current = now or datetime.now(timezone.utc)
    cutoff = current.timestamp() - STALE_RUNNING_AFTER_SEC
    live = {tuple(key) for key in live_keys}
    dead: list[tuple[Any, ...]] = []
    for row in conn.execute(
        "SELECT article_id, direction_id, research_version, status, scored_at "
        "FROM article_bv_direction WHERE status IN (?, ?)",
        (STATUS_RUNNING, STATUS_QUEUED),
    ).fetchall():
        status = str(row["status"])
        if status == STATUS_QUEUED:
            key = (str(row["article_id"]), int(row["direction_id"]), int(row["research_version"]))
            if key in live:
                continue
            dead.append((STATUS_FAILED, "服务重启，队列已中断", row["article_id"], row["direction_id"], row["research_version"]))
            continue
        try:
            started = datetime.fromisoformat(row["scored_at"])
        except (TypeError, ValueError):
            started = None
        if started is None:
            dead.append((STATUS_FAILED, "打分开始时间无法解析", row["article_id"], row["direction_id"], row["research_version"]))
            continue
        if started.tzinfo is None:
            started = started.replace(tzinfo=timezone.utc)
        if started.timestamp() < cutoff:
            dead.append((STATUS_FAILED, "打分超时", row["article_id"], row["direction_id"], row["research_version"]))
    for status, error, article_id, direction_id, research_version in dead:
        conn.execute(
            "UPDATE article_bv_direction SET status=?, error=?, scored_at=? "
            "WHERE article_id=? AND direction_id=? AND research_version=? "
            "AND status IN (?, ?)",
            (
                status,
                error,
                _now(),
                article_id,
                direction_id,
                research_version,
                STATUS_QUEUED,
                STATUS_RUNNING,
            ),
        )
    if dead:
        conn.commit()
    return len(dead)


__all__ = [
    "DEFAULT_BUDGET_S",
    "DEFAULT_STEP_TIMEOUT",
    "INFLIGHT_STATUSES",
    "MAX_BATCH",
    "MAX_REQUEST",
    "SETTLED_STATUSES",
    "STATUS_FAILED",
    "STATUS_INSUFFICIENT",
    "STATUS_NEEDS_REVIEW",
    "STATUS_QUEUED",
    "STATUS_RUNNING",
    "STATUS_SCORED",
    "article_hashes",
    "load_scores",
    "mark_queued",
    "mark_running",
    "map_status",
    "recover_stale",
    "resolve_direction",
    "resolve_rubric",
    "score_one",
    "write_score",
]
