"""moni.share.manifest — write share manifest to DB queue.

The manifest pipeline:
1. Allocates a UUID share_id.
2. Inserts a 'pending' row into share_tasks.
3. Writes a queue file at ~/.moni/queue/<id>.json (after DB commit).
4. Returns the share_id.

All DB work is transaction-scoped: on any error the transaction is rolled back
and the queue file is NOT written.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Queue directory relative to home
_QUEUE_DIR_NAME = ".moni/queue"


def resolve_queue_dir(*, create: bool = False) -> Path:
    """Resolve the share-queue directory from one shared rule.

    ``MONI_QUEUE_DIR`` wins when set; otherwise ``$HOME/.moni/queue``.
    Every producer and consumer (manifest writes, worker locks, worker
    scans) must use this function so a custom queue can never split the
    system into two directories that never see each other's files.

    Args:
        create: Create the directory (parents included) when missing.
    """
    env_dir = os.environ.get("MONI_QUEUE_DIR")
    if env_dir:
        qdir = Path(env_dir).expanduser()
    else:
        qdir = Path(os.path.expanduser("~")) / _QUEUE_DIR_NAME
    if create:
        qdir.mkdir(parents=True, exist_ok=True)
    return qdir


def _queue_dir() -> Path:
    """Return the queue directory, creating it if necessary."""
    return resolve_queue_dir(create=True)


def _db_connect() -> Any:
    """Return a DB connection, using MONI_DB_PATH when set (for test isolation)."""
    import sqlite3

    db_path = os.environ.get("MONI_DB_PATH")
    if db_path:
        conn = sqlite3.connect(db_path, check_same_thread=False)
    else:
        # Import the singleton from moni.db.connection
        from moni.db.connection import get_db

        conn = get_db()
    conn.row_factory = sqlite3.Row
    return conn


def write_manifest(
    share_id: str | None,
    validated_payload: dict[str, Any],
    *,
    format_name: str | None = None,
) -> str:
    """Write a share manifest: DB row + queue file.

    Args:
        share_id:         Caller may pass a specific ID, or None to generate one.
        validated_payload: The validated format-specific payload dict
            (already passed through ``validate_manifest()``).
        format_name:      Format string, e.g. ``'markdown'``. Extracted from
            ``validated_payload['format']`` if not supplied.

    Returns:
        The share_id (caller-supplied or newly generated).

    Raises:
        OSError: if the queue file cannot be written after the DB commit.
        sqlite3.Error: for database errors (transaction rolled back).
    """
    if share_id is None:
        share_id = secrets.token_urlsafe(16)

    now = datetime.now(timezone.utc).isoformat()

    # Resolve format — allow caller to pass it directly or pull from payload
    if format_name is None:
        format_name = validated_payload.get("format", "unknown")

    # The manifest document written to the queue file
    manifest_doc: dict[str, Any] = {
        "share_id": share_id,
        "created_at": now,
        "status": "pending",
        "format": format_name,
        "payload": validated_payload,
    }

    # --- DB transaction -------------------------------------------------
    conn = _db_connect()
    try:
        article_ids = validated_payload.get("article_ids", [])

        conn.execute(
            """
            INSERT INTO share_tasks (id, format, article_ids, status, created_at)
            VALUES (?, ?, ?, 'pending', ?)
            """,
            (share_id, format_name, json.dumps(article_ids), now),
        )
        conn.commit()
        logger.info("share_tasks row created: id=%s format=%s", share_id, format_name)
    except Exception:
        conn.rollback()
        logger.exception("DB rollback for share_id=%s", share_id)
        raise

    # --- Queue file (after DB commit) ---------------------------------
    queue_path = _queue_dir() / f"{share_id}.json"
    try:
        queue_path.write_text(json.dumps(manifest_doc, indent=2, ensure_ascii=False))
        logger.info("queue file written: %s", queue_path)
    except OSError:
        # Post-commit failure: log and re-raise — the DB row is durable.
        # The caller/operator must handle this (e.g. retry the file write).
        logger.error("queue file write FAILED for share_id=%s: %s", share_id, queue_path)
        raise

    return share_id
