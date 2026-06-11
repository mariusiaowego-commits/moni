"""moni.llm.usage — Parse llm_usage.log and aggregate token usage.

The log file (data/llm_usage.log) contains one JSON object per line,
written by TokenLogger.log().  Each entry has:
  ts, model, prompt_tokens, completion_tokens, total_tokens, module, extra

This module reads the log, filters by time range, and aggregates
by module / model / date for the LLM settings UI.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

# Default log path — resolved from project root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_DEFAULT_LOG = _REPO_ROOT / "data" / "llm_usage.log"


@dataclass
class UsageEntry:
    """A single parsed log entry."""
    ts: datetime
    model: str
    prompt_tokens: int
    completion_tokens: int
    module: str
    provider: str


def parse_log(log_path: Path | str | None = None) -> list[UsageEntry]:
    """Read JSON lines from the log file, return parsed entries.

    Skips malformed lines (logged as warnings).
    """
    path = Path(log_path) if log_path else _DEFAULT_LOG
    if not path.is_file():
        return []

    entries: list[UsageEntry] = []
    with open(path, "r", encoding="utf-8") as f:
        for lineno, line in enumerate(f, 1):
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
                ts_str = d.get("ts", "")
                try:
                    ts = datetime.fromisoformat(ts_str)
                except (ValueError, TypeError):
                    ts = datetime.now(timezone.utc)

                extra = d.get("extra", {})
                entries.append(UsageEntry(
                    ts=ts,
                    model=d.get("model", "unknown"),
                    prompt_tokens=int(d.get("prompt_tokens", 0)),
                    completion_tokens=int(d.get("completion_tokens", 0)),
                    module=d.get("module", ""),
                    provider=extra.get("provider", "") if isinstance(extra, dict) else "",
                ))
            except (json.JSONDecodeError, KeyError, TypeError) as exc:
                log.warning("usage: skip line %d: %s", lineno, exc)
    return entries


def aggregate(
    entries: list[UsageEntry],
    *,
    days: int = 30,
    group_by: str = "module",
) -> dict[str, Any]:
    """Aggregate entries into summary + grouped breakdown.

    Args:
        entries: Parsed log entries.
        days: Number of days to look back.  -1 = all time.
        group_by: Grouping key — "module", "model", or "date".

    Returns:
        Dict with keys: period, summary, by_module, by_model, by_date.
        Only the group matching ``group_by`` is populated; others are empty lists.
    """
    now = datetime.now(timezone.utc)

    # Filter by time range
    if days > 0:
        cutoff = now - timedelta(days=days)
        entries = [e for e in entries if e.ts >= cutoff]

    # Summary
    total_calls = len(entries)
    total_prompt = sum(e.prompt_tokens for e in entries)
    total_completion = sum(e.completion_tokens for e in entries)

    # Group
    groups: dict[str, dict[str, int]] = {}
    for e in entries:
        if group_by == "module":
            key = e.module or "(未标记)"
        elif group_by == "model":
            key = e.model
        elif group_by == "date":
            key = e.ts.strftime("%Y-%m-%d")
        else:
            key = e.module or "(未标记)"

        if key not in groups:
            groups[key] = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}
        groups[key]["calls"] += 1
        groups[key]["prompt_tokens"] += e.prompt_tokens
        groups[key]["completion_tokens"] += e.completion_tokens

    # Build grouped lists
    def _sorted_list(key_name: str) -> list[dict]:
        return sorted(
            [{key_name: k, **v} for k, v in groups.items()],
            key=lambda x: x["prompt_tokens"],
            reverse=True,
        )

    by_module = _sorted_list("module") if group_by == "module" else []
    by_model = _sorted_list("model") if group_by == "model" else []
    by_date = (
        sorted(
            [{"date": k, **v} for k, v in groups.items()],
            key=lambda x: x["date"],
        )
        if group_by == "date"
        else []
    )

    # Period
    if entries:
        period_from = min(e.ts for e in entries).strftime("%Y-%m-%d")
        period_to = max(e.ts for e in entries).strftime("%Y-%m-%d")
    else:
        period_from = period_to = now.strftime("%Y-%m-%d")

    return {
        "period": {"from": period_from, "to": period_to},
        "summary": {
            "total_calls": total_calls,
            "total_prompt_tokens": total_prompt,
            "total_completion_tokens": total_completion,
        },
        "by_module": by_module,
        "by_model": by_model,
        "by_date": by_date,
    }
