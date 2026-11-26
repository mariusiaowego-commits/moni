"""Default share titles: 同业简报-YYMMDD-<格式>-NN.

Numbering is per format per local day.  The number is a human-readable
label, not a uniqueness key: ``allocate_title`` only reads the current max
under a short ``BEGIN IMMEDIATE``; the row itself is inserted afterwards by
``write_manifest``, so two truly concurrent creations can still pick the
same number.  The single worker queue makes that a non-issue in practice.
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any

from moni.share.manifest import resolve_queue_dir

TITLE_FORMAT_LABELS = {
    "html_landing": "网页",
    "info_graphic_png": "信息图",
    "report_png": "报告图",
    "pdf": "PDF",
    "markdown": "Markdown",
    "rich_text": "富文本",
    "plain_text": "纯文本",
    "case_brief_html": "案例",
}

TITLE_FORMATS = frozenset(TITLE_FORMAT_LABELS)


def title_label(format_name: str) -> str:
    return TITLE_FORMAT_LABELS.get(format_name, format_name)


def title_prefix(format_name: str, *, base: str = "同业简报", now: datetime | None = None) -> str:
    day = (now or datetime.now()).strftime("%y%m%d")
    return f"{base}-{day}-{title_label(format_name)}-"


def _queue_title(task_id: str) -> str | None:
    path = resolve_queue_dir() / f"{task_id}.json"
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    payload = doc.get("payload") if isinstance(doc, dict) else None
    if not isinstance(payload, dict):
        return None
    title = payload.get("title")
    return str(title).strip() if title else None


def _field(row: Any, key: str) -> Any:
    try:
        return row[key]
    except (IndexError, KeyError, TypeError):
        return None


def _task_title(row: Any) -> str | None:
    raw = _field(row, "extra")
    if raw:
        try:
            doc = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            doc = None
        if isinstance(doc, dict) and doc.get("title"):
            return str(doc["title"]).strip()
    task_id = _field(row, "id")
    return _queue_title(str(task_id)) if task_id else None


def _utc_day_bounds(now: datetime) -> tuple[str, str]:
    local_midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    local_next = local_midnight + timedelta(days=1)
    return (
        local_midnight.astimezone(timezone.utc).isoformat(),
        local_next.astimezone(timezone.utc).isoformat(),
    )


def allocate_title(
    conn: sqlite3.Connection,
    format_name: str,
    *,
    base: str = "同业简报",
    now: datetime | None = None,
    preview: bool = False,
) -> str:
    """Return the next label.  ``preview`` skips the write lock (GET path)."""
    now = now or datetime.now()
    prefix = title_prefix(format_name, base=base, now=now)
    pattern = re.compile(re.escape(prefix) + r"(\d{1,4})")
    day_start, day_end = _utc_day_bounds(now)

    began = False
    if not preview:
        try:
            conn.execute("BEGIN IMMEDIATE")
            began = True
        except sqlite3.OperationalError:
            # Already inside a transaction; the read below is still safe.
            began = False
    try:
        rows = conn.execute(
            """
            SELECT id, extra FROM share_tasks
            WHERE format = ? AND created_at >= ? AND created_at < ?
            ORDER BY created_at ASC
            """,
            (format_name, day_start, day_end),
        ).fetchall()
        seqs: list[int] = []
        untitled = 0
        for row in rows:
            title = _task_title(row)
            match = pattern.fullmatch(title) if title else None
            if match:
                seqs.append(int(match.group(1)))
            else:
                untitled += 1
        seq = max(seqs + [untitled]) + 1 if (seqs or untitled) else 1
        return f"{prefix}{seq:02d}"
    finally:
        if began:
            conn.rollback()
