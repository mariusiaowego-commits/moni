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
from moni.web.exceptions import BadRequestError, NotFoundError
from moni.web.models import ShareTask, ShareTaskCreateV1, ShareTaskListResponse, ShareTaskResponseV1
logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/share", tags=["share"])


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
        created_at=row["created_at"],
        started_at=row.get("started_at"),
        finished_at=row.get("finished_at"),
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
               created_at, started_at, finished_at, extra
        FROM share_tasks WHERE id = ?
        """,
        (task_id,),
    ).fetchone()

    if row is None:
        raise NotFoundError(message=f"Share task {task_id!r} not found")

    return _row_to_share_task(dict(row))
