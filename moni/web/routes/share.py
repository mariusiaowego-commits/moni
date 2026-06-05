"""routes/share — share-task creation, listing and single-task retrieval.

The ``share_tasks`` table stores share/export tasks with a JSON article_ids array.
POST /api/share uses the v1.0 manifest pipeline:
  1. Validate body against moni.share.formats (BadRequestError on failure).
  2. Call write_manifest() which creates a DB row and a ~/.moni/queue/<id>.json file.
  3. Return 201 with the share_id.
"""

from __future__ import annotations

import json
import logging
import secrets
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Query

from moni.db.connection import get_db
from moni.share.formats import validate_manifest
from moni.share.manifest import write_manifest
from moni.web.exceptions import BadRequestError, NotFoundError
from moni.web.models import ShareTask, ShareTaskCreateV1, ShareTaskListResponse, ShareTaskResponseV1

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/share", tags=["share"])


def _row_to_share_task(row: dict) -> ShareTask:
    return ShareTask(
        id=row["id"],
        article_id=0,  # legacy: share_tasks stores JSON article_ids, not single article_id
        token=row.get("id", ""),
        expires_at=row["created_at"],
        view_count=0,
        created_at=row["created_at"],
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
        SELECT id, format, article_ids, status, error,
               created_at, started_at, finished_at, extra
        FROM share_tasks
        ORDER BY created_at DESC
        LIMIT ? OFFSET ?
        """,
        (page_size, offset),
    ).fetchall()

    total = conn.execute("SELECT COUNT(*) FROM share_tasks").fetchone()[0]

    tasks = []
    for row in rows:
        tasks.append(
            ShareTask(
                id=row["id"],
                article_id=0,
                token=row["id"],
                expires_at=row["created_at"],
                view_count=0,
                created_at=row["created_at"],
            )
        )

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
        SELECT id, format, article_ids, status, error,
               created_at, started_at, finished_at, extra
        FROM share_tasks WHERE id = ?
        """,
        (task_id,),
    ).fetchone()

    if row is None:
        raise NotFoundError(message=f"Share task {task_id!r} not found")

    return ShareTask(
        id=row["id"],
        article_id=0,
        token=row["id"],
        expires_at=row["created_at"],
        view_count=0,
        created_at=row["created_at"],
    )
