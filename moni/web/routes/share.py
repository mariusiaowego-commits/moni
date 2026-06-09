"""routes/share — share-task creation, listing and single-task retrieval.

The ``share_tasks`` table stores share/export tasks with a JSON article_ids array.
POST /api/share uses the v1.0 manifest pipeline:
  1. Validate body against moni.share.formats (BadRequestError on failure).
  2. Call write_manifest() which creates a DB row and a ~/.moni/queue/<id>.json file.
  3. Return 201 with the share_id.

v1.0.5.15: GET /api/share/tasks now also returns ``output_url`` — a web URL
that the frontend can <a href=> directly (browser-safe).  Backed by the
static mount at /artifacts/ + ensure_artifact() in moni.share.artifacts
which copies pre-v1.0.5.15 done rows' output_path into data/artifacts/.

v1.0.5.16: POST /api/share/estimate — pre-flight estimate for the
confirm-modal.  Reuses validate_manifest so the user sees the same
validation errors as the real create.  No DB write, no share_task row.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.share.artifacts import ARTIFACTS_DIR, ensure_artifact
from moni.share.formats import validate_manifest
from moni.share.manifest import write_manifest
from moni.share.worker import LOG_FILE, launch_worker, tail_log
from moni.web.exceptions import BadRequestError, NotFoundError
from moni.web.models import (
    ShareTask,
    ShareTaskCreateV1,
    ShareTaskEstimateResponse,
    ShareTaskListResponse,
    ShareTaskResponseV1,
)
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/share", tags=["share"])


# ---------------------------------------------------------------------------
# v1.0.5.16: estimate helpers (token count, duration, model resolution)
# ---------------------------------------------------------------------------

# Rough char→token heuristic for CJK-mixed content.  CJK characters are
# 1 char ≈ 1.5 tokens (each CJK glyph is one BPE token typically), while
# Latin runs are 4 chars ≈ 1 token.  We use 2.5 chars/token as a
# middle-of-road number — accurate to within ±30% in practice, which
# is good enough for a confirm-modal.
_CHARS_PER_TOKEN = 2.5

# Output-token multipliers per format, applied to the input estimate.
# These come from eyeballing what each format actually produces: ascii
# variants are tight (output < input), html_landing/ppt_slides are
# verbose (output ≈ 1.5-2x input because of styling + boilerplate).
_OUTPUT_MULTIPLIER = {
    "plain_text": 0.8,
    "markdown": 1.0,
    "html_landing": 1.8,
    "pdf_bound": 1.2,
    "ppt_slides": 1.5,
    "ascii_art": 0.4,
    "ascii_video": 0.6,
    "architecture_svg": 0.5,
    "info_graphic_png": 0.3,   # LLM produces a brief description, image is rendered
    "knowledge_comic": 0.6,
}

# Last-N done share_tasks to average for duration estimate.
_DURATION_HISTORY_WINDOW = 5

# Fallback duration if we have no history for this format (seconds).
_DURATION_FALLBACK_SEC = {
    "html_landing": 90,
    "info_graphic_png": 120,   # infographic generation tends to be slow
    "pdf_bound": 90,
    "ppt_slides": 90,
    "architecture_svg": 60,
    "knowledge_comic": 90,
    "ascii_video": 60,
    "ascii_art": 30,
    "markdown": 30,
    "plain_text": 20,
}


def _resolve_active_model() -> tuple[str, str, str]:
    """Read llm_settings → llm_models → llm_providers.

    Returns ``(model_display, model_id, provider_display)``.  Falls
    back to placeholder values if the chain is broken (e.g. a freshly
    seeded DB that never had llm_settings populated).  We never raise
    here — the estimate is best-effort.
    """
    try:
        conn = get_db()
        row = conn.execute(
            "SELECT value FROM llm_settings WHERE key='active_summary_model_id'"
        ).fetchone()
        if not row:
            return ("(model unset)", "(model unset)", "(provider unset)")
        model_id = row["value"]

        model_row = conn.execute(
            "SELECT model_id, display_name, provider_id FROM llm_models WHERE id=?",
            (model_id,),
        ).fetchone()
        if not model_row:
            return (model_id, model_id, "(provider unknown)")
        model_name = model_row["display_name"] or model_row["model_id"] or model_id

        provider_row = conn.execute(
            "SELECT name FROM llm_providers WHERE id=?",
            (model_row["provider_id"],),
        ).fetchone()
        provider_name = (
            provider_row["name"] if provider_row else "(provider unknown)"
        )
        return (model_name, model_id, provider_name)
    except Exception as exc:  # noqa: BLE001 — never let estimate crash
        logger.warning("estimate: _resolve_active_model failed: %r", exc)
        return ("(lookup failed)", "(lookup failed)", "(lookup failed)")


def _estimate_article_chars(article_ids: list[str]) -> tuple[int, int]:
    """Sum raw_content length for the given article ids.

    Returns ``(articles_count, total_chars)``.  Missing or deleted
    articles are silently skipped (they show as 0 chars).  The user
    can compare articles_count vs the UI selection to spot mismatches.
    """
    if not article_ids:
        return (0, 0)
    conn = get_db()
    placeholders = ",".join("?" * len(article_ids))
    rows = conn.execute(
        f"SELECT LENGTH(IFNULL(raw_content, '')) AS chars "
        f"FROM articles WHERE id IN ({placeholders})",
        article_ids,
    ).fetchall()
    total = sum((r["chars"] or 0) for r in rows)
    return (len(rows), total)


def _estimate_duration(format_name: str) -> tuple[int, int]:
    """Average wall-clock seconds for the last N done tasks of format.

    Returns ``(avg_seconds, sample_count)``.  If we have no history,
    fall back to a per-format static default.  ``sample_count`` is
    the number of past tasks we averaged (0 if fallback).
    """
    conn = get_db()
    rows = conn.execute(
        """
        SELECT started_at, finished_at FROM share_tasks
        WHERE format = ? AND status = 'done'
          AND started_at IS NOT NULL AND finished_at IS NOT NULL
        ORDER BY finished_at DESC
        LIMIT ?
        """,
        (format_name, _DURATION_HISTORY_WINDOW),
    ).fetchall()
    if not rows:
        fallback = _DURATION_FALLBACK_SEC.get(format_name, 60)
        return (fallback, 0)

    durations: list[int] = []
    for r in rows:
        try:
            started = datetime.fromisoformat(str(r["started_at"]))
            finished = datetime.fromisoformat(str(r["finished_at"]))
            durations.append(max(1, int((finished - started).total_seconds())))
        except (TypeError, ValueError):
            continue
    if not durations:
        fallback = _DURATION_FALLBACK_SEC.get(format_name, 60)
        return (fallback, 0)
    return (int(sum(durations) / len(durations)), len(durations))


def _row_to_share_task(row: dict[str, Any]) -> ShareTask:
    """Convert a share_tasks DB row to the v1.0.5.14+ ShareTask shape.

    The DB stores ``article_ids`` as a JSON string (e.g. '["id1","id2"]');
    we parse it to count the articles for the frontend queue display.

    v1.0.5.15: also surface ``output_url`` for done tasks.  The
    ``output_path`` is the absolute filesystem path (for the user to
    see/copy); ``output_url`` is a browser-safe web URL pointing at the
    artifact under /artifacts/<task_id>-<format>.<ext>.  Pre-v1.0.5.15
    done rows have their files copied into data/artifacts/ on demand
    via ensure_artifact() (idempotent).  Missing source files yield
    output_url=None (frontend shows plain text instead of a link).
    """
    article_ids_raw = row.get("article_ids") or "[]"
    try:
        article_count = len(json.loads(article_ids_raw))
    except (TypeError, json.JSONDecodeError):
        article_count = 0

    output_path = row.get("output_path")
    output_url: str | None = None
    if row.get("status") == "done" and output_path:
        artifact_name = ensure_artifact(
            task_id=row["id"],
            format_name=row.get("format", ""),
            output_path=output_path,
        )
        if artifact_name:
            output_url = f"/artifacts/{artifact_name}"

    return ShareTask(
        id=row["id"],
        format=row["format"],
        article_count=article_count,
        # v1.0.5.15: surface output_path + output_url for done tasks.
        # v1.0.5.14 helper omitted these, so the frontend "打开" link
        # had no target.  output_path is the absolute filesystem path
        # (user-readable); output_url is the browser-safe web URL.
        output_path=output_path,
        output_url=output_url,
        status=row.get("status", "pending"),
        error=row.get("error"),
        # v1.0.5.17: 3 progress fields pass-through.  The worker
        # (default_worker.sh) writes these at each stage transition
        # so the user can see where a running task is without
        # opening the modal.  Safe defaults (None) for pre-v1.0.5.17
        # rows that were backfilled with CASE/CASE in the migration.
        progress_stage=row.get("progress_stage"),
        progress_pct=row.get("progress_pct"),
        progress_message=row.get("progress_message"),
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
    )


@router.post("/estimate", response_model=ShareTaskEstimateResponse)
def estimate_share_task(body: ShareTaskCreateV1) -> ShareTaskEstimateResponse:
    """Pre-flight estimate for the v1.0.5.16 confirm-modal.

    Reuses ``validate_manifest`` so the user sees the exact same
    validation errors as the real ``POST /api/share`` — no surprise
    rejections after they click 确认.  No DB write, no share_task
    row, no queue file.

    Response fields:
    - model_name/id/provider_name: from llm_settings → llm_models → llm_providers
    - articles_count / total_chars: SQL aggregate over articles.raw_content
    - input_tokens_est: total_chars / 2.5 (CJK-mixed heuristic)
    - output_tokens_est: input * format-specific multiplier
    - duration_sec_est: avg of last 5 done share_tasks for the format,
      fallback to per-format static defaults
    - artifact_path_template: where the user will find the file once
      the worker writes it
    """
    # 1. Same validation as the real create.  BadRequestError bubbles
    # up to a 400 — the modal should not appear in the first place.
    format_name, validated_payload = validate_manifest(body.model_dump(mode="json"))

    # 2. Pull article_ids from the validated payload (not the raw body,
    # so we know they've been normalized by the format-specific model).
    article_ids_raw = validated_payload.get("article_ids") or []
    if not isinstance(article_ids_raw, list):
        article_ids_raw = []
    article_ids: list[str] = [str(x) for x in article_ids_raw if x]

    # 3. Resolve model + size + duration in parallel-friendly order.
    model_name, model_id, provider_name = _resolve_active_model()
    articles_count, total_chars = _estimate_article_chars(article_ids)
    duration_sec, duration_basis = _estimate_duration(format_name)

    # 4. Token estimates.
    input_tokens = int(total_chars / _CHARS_PER_TOKEN) if total_chars else 0
    output_mult = _OUTPUT_MULTIPLIER.get(format_name, 1.0)
    output_tokens = int(input_tokens * output_mult)

    # 5. Artifact path is illustrative — the real id is allocated by
    # write_manifest() at create time.  We mirror the same filename
    # pattern as moni/share/artifacts.py:ensure_artifact() so the
    # path the user sees matches where the file actually lands.
    artifact_template = f"/artifacts/<id>-{format_name}.<ext>"

    logger.info(
        "share estimate: format=%s articles=%d chars=%d "
        "input_tokens=%d output_tokens=%d duration=%ds (n=%d)",
        format_name, articles_count, total_chars,
        input_tokens, output_tokens, duration_sec, duration_basis,
    )

    return ShareTaskEstimateResponse(
        model_name=model_name,
        model_id=model_id,
        provider_name=provider_name,
        articles_count=articles_count,
        total_chars=total_chars,
        input_tokens_est=input_tokens,
        output_tokens_est=output_tokens,
        duration_sec_est=duration_sec,
        duration_sec_basis=duration_basis,
        artifact_path_template=artifact_template,
    )


@router.post("", response_model=ShareTaskResponseV1, status_code=201)
def create_share_task(body: ShareTaskCreateV1) -> ShareTaskResponseV1:
    """Create a share task via the v1.0 manifest pipeline.

    Validates the request body against moni.share.formats, then calls
    ``write_manifest()`` which inserts a 'pending' row in share_tasks and
    writes a queue file at ~/.moni/queue/<id>.json.
    """
    # 1. Validate against v1.0 format schemas — raises BadRequestError on failure
    format_name, validated_payload = validate_manifest(
        body.model_dump(mode="json")
    )

    # 2. Persist via write_manifest — DB row + queue file
    share_id = write_manifest(
        share_id=None,
        validated_payload=validated_payload,
        format_name=format_name,
    )

    logger.info(
        "share task created: share_id=%s format=%s", share_id, format_name
    )

    # 3. Return 201
    return ShareTaskResponseV1(share_id=share_id, status="pending")


@router.get("/tasks", response_model=ShareTaskListResponse)
def list_share_tasks(
    page: int = Query(1, ge=1, description="Page number (1-indexed)"),
    page_size: int = Query(20, ge=1, le=100, description="Items per page"),
) -> ShareTaskListResponse:
    """List share tasks, most recent first, paginated."""
    conn = get_db()
    offset = (page - 1) * page_size

    rows = conn.execute(
        """
        SELECT id, format, article_ids, output_path, status, error,
               progress_stage, progress_pct, progress_message,
               created_at, started_at, finished_at, extra
        FROM share_tasks
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (page_size, offset),
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM share_tasks").fetchone()[0]

    tasks = [_row_to_share_task(dict(row)) for row in rows]

    return ShareTaskListResponse(
        items=tasks,
        total=total,
        page=page,
        page_size=page_size,
    )


@router.get("/tasks/{task_id}", response_model=ShareTask)
def get_share_task(task_id: str) -> ShareTask:
    """Fetch a single share task by its string ID (UUID)."""
    conn = get_db()
    row = conn.execute(
        """
        SELECT id, format, article_ids, output_path, status, error,
               progress_stage, progress_pct, progress_message,
               created_at, started_at, finished_at, extra
        FROM share_tasks WHERE id = ?
        """,
        (task_id,),
    ).fetchone()

    if row is None:
        raise NotFoundError(message=f"Share task {task_id!r} not found")

    return _row_to_share_task(dict(row))


# ---------------------------------------------------------------------------
# v1.0.5.17: worker on-demand trigger + log tail
# ---------------------------------------------------------------------------
# The UI's "▶ 立即处理队列" button hits POST /api/share/worker/run
# which spawns default_worker.sh as a detached subprocess.  The
# 30s queue-status polling (already in app.js) then surfaces the
# pending → running → done transitions and progress_stage column.
# ---------------------------------------------------------------------------


@router.post("/worker/run")
def run_worker() -> dict:
    """Spawn the default worker subprocess on demand.

    Response shape (always 200 unless the worker script itself is
    missing — the UI prefers an error body over a 5xx so the modal
    can show the message verbatim):

    - {started: true,  pid: N, log_path: '...'}   — we just spawned it
    - {started: false, already_running: true, pid: N, log_path: '...'}
      — another worker was already up; no-op, UI tells the user
    - {started: false, already_running: false, pid: null,
       error: '...', log_path: '...'}
      — failed (e.g. worker script missing); UI shows the error
    """
    result = launch_worker()
    if result.error:
        logger.warning("worker launch failed: %s", result.error)
    return {
        "started": result.started,
        "already_running": result.already_running,
        "pid": result.pid,
        "log_path": result.log_path,
        "error": result.error,
    }


@router.get("/worker/log")
def read_worker_log(tail: int = Query(200, ge=1, le=5000)) -> dict:
    """Return the last ``tail`` lines (default 200) of worker.log.

    The frontend modal polls this every 2-3s while a worker is
    running so the user can see live progress (hermes chat output
    snippets, "DONE task_id", etc.) without tailing the file by
    hand.  Returns ``{lines: [...], log_path: '...'}``.
    """
    body = tail_log(n=tail)
    return {
        "lines": body.splitlines() if body else [],
        "log_path": str(LOG_FILE),
    }
