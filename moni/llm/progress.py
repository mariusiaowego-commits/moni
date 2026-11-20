"""Process-local live progress for running LLM jobs."""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from typing import Any

_LOCK = threading.Lock()
_JOBS: dict[str, dict[str, Any]] = {}

STAGES = ("thinking", "searching", "writing")


def start_job(job_id: str) -> None:
    with _LOCK:
        _JOBS[job_id] = _snapshot("thinking", 0)


def record_event(job_id: str, event: dict[str, Any]) -> None:
    with _LOCK:
        current = _JOBS.get(job_id)
        if current is None:
            return
        _JOBS[job_id] = _reduce_event(current, event)


def get_job(job_id: str) -> dict[str, Any] | None:
    with _LOCK:
        current = _JOBS.get(job_id)
        return dict(current) if current is not None else None


def finish_job(job_id: str) -> None:
    with _LOCK:
        _JOBS.pop(job_id, None)


def _reduce_event(current: dict[str, Any], event: dict[str, Any]) -> dict[str, Any]:
    event_type = str(event.get("type") or "")
    if event_type == "text":
        return _snapshot("writing", current["searches"], current["last_query"])
    if event_type != "tool_use":
        return current

    query = _search_query(event)
    if query is None:
        return current
    return _snapshot(
        "searching",
        int(current["searches"]) + 1,
        query,
    )


def _search_query(event: dict[str, Any]) -> str | None:
    name = str(event.get("name") or "")
    if "search" not in name and "browser" not in name:
        return None
    args = event.get("input")
    if not isinstance(args, dict):
        return name
    for key in ("query", "q", "search_query", "keyword", "keywords"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()[:80]
        if isinstance(value, list) and value:
            return " ".join(str(item) for item in value)[:80]
    return name


def _snapshot(
    stage: str,
    searches: int,
    last_query: str = "",
) -> dict[str, Any]:
    return {
        "stage": stage,
        "searches": int(searches),
        "last_query": str(last_query),
        "last_event_at": datetime.now(timezone.utc).isoformat(),
    }
