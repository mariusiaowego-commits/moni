"""registry — cron 任务注册表（sprint-69 cron-console）。

任务定义从 ``config/schedule.yaml`` 迁到 DB 表 ``cron_jobs``：
- migration 056 建表并从 YAML seed 一次；
- 调度器启动时读 DB，空表则回落 YAML；
- 修改/启停通过 API 写 DB 并 bump ``cron_meta.schedule_revision``，
  调度器 15s tick 发现 revision 变化后热加载，无需重启进程。
"""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml
from zoneinfo import ZoneInfo

CST = ZoneInfo("Asia/Shanghai")

DAYS = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]
_DAY_INDEX = {d: i for i, d in enumerate(DAYS)}

_META_REVISION = "schedule_revision"
_META_HEARTBEAT = "heartbeat_at"


class CronRegistryError(ValueError):
    """Invalid job definition or unknown job id."""


class AlreadyQueued(CronRegistryError):
    """A run request for this job is already pending or claimed."""


def parse_days(spec: str) -> list[int]:
    """Parse ``mon-fri`` / ``mon,wed`` / ``*`` into a sorted weekday list (mon=0)."""
    raw = (spec or "").strip().lower()
    if raw in ("*", "all", "mon-sun", "sun-mon"):
        return list(range(7))
    out: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            start, end = (p.strip() for p in part.split("-", 1))
            if start not in _DAY_INDEX or end not in _DAY_INDEX:
                raise CronRegistryError(f"非法星期区间: {part}")
            i, j = _DAY_INDEX[start], _DAY_INDEX[end]
            span = range(i, j + 1) if i <= j else list(range(i, 7)) + list(range(0, j + 1))
            out.update(span)
        else:
            if part not in _DAY_INDEX:
                raise CronRegistryError(f"非法星期: {part}")
            out.add(_DAY_INDEX[part])
    if not out:
        raise CronRegistryError("至少需要一天")
    return sorted(out)


def format_days(days: list[int]) -> str:
    """Inverse of :func:`parse_days` — canonical string form."""
    uniq = sorted(set(days))
    if not uniq:
        raise CronRegistryError("至少需要一天")
    if uniq == list(range(7)):
        return "*"
    if uniq == list(range(5)):
        return "mon-fri"
    if uniq == [5, 6]:
        return "sat,sun"
    return ",".join(DAYS[i] for i in uniq)


def next_run_at(hour: int, minute: int, day_of_week: str, now: datetime | None = None) -> datetime | None:
    """Next Asia/Shanghai fire time strictly after ``now`` (None if schedule invalid)."""
    current = now or datetime.now(CST)
    if current.tzinfo is None:
        current = current.replace(tzinfo=CST)
    try:
        days = set(parse_days(day_of_week))
    except CronRegistryError:
        return None
    for offset in range(0, 8):
        day = (current + timedelta(days=offset)).date()
        if day.weekday() not in days:
            continue
        candidate = datetime(day.year, day.month, day.day, hour, minute, tzinfo=CST)
        if candidate > current:
            return candidate
    return None


def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
    try:
        kwargs = json.loads(row["func_kwargs"] or "{}")
    except json.JSONDecodeError:
        kwargs = {}
    return {
        "id": row["id"],
        "name": row["name"],
        "func_path": row["func_path"],
        "trigger_type": row["trigger_type"],
        "hour": int(row["hour"]),
        "minute": int(row["minute"]),
        "day_of_week": row["day_of_week"],
        "func_kwargs": kwargs,
        "is_active": bool(row["is_active"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get_job(conn: sqlite3.Connection, job_id: str) -> dict[str, Any] | None:
    row = conn.execute("SELECT * FROM cron_jobs WHERE id = ?", (job_id,)).fetchone()
    return _row_to_job(row) if row else None


def list_jobs(conn: sqlite3.Connection, *, active_only: bool = False) -> list[dict[str, Any]]:
    sql = "SELECT * FROM cron_jobs"
    if active_only:
        sql += " WHERE is_active = 1"
    sql += " ORDER BY hour, minute, id"
    return [_row_to_job(r) for r in conn.execute(sql).fetchall()]


def seed_from_yaml(conn: sqlite3.Connection, path: Path | str, *, force: bool = False) -> int:
    """Insert YAML job definitions into ``cron_jobs`` (idempotent).

    Returns the number of rows inserted.  Skips entirely when the table already
    has rows, unless ``force`` is set.
    """
    existing = conn.execute("SELECT COUNT(*) FROM cron_jobs").fetchone()[0]
    if existing and not force:
        return 0

    config_path = Path(path)
    if not config_path.exists():
        return 0

    data = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    now = datetime.now(CST).isoformat()
    inserted = 0
    for cfg in data.get("jobs", []):
        day = str(cfg.get("day_of_week", "*"))
        parse_days(day)  # validate
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO cron_jobs
                (id, name, func_path, trigger_type, hour, minute, day_of_week,
                 func_kwargs, is_active, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                cfg["id"],
                cfg.get("name", cfg["id"]),
                cfg["func_path"],
                cfg.get("trigger_type", "cron"),
                int(cfg.get("hour", 0)),
                int(cfg.get("minute", 0)),
                day,
                json.dumps(cfg.get("func_kwargs") or {}, ensure_ascii=False),
                int(bool(cfg.get("is_active", True))),
                now,
                now,
            ),
        )
        inserted += cursor.rowcount
    conn.commit()
    return inserted


def update_job(conn: sqlite3.Connection, job_id: str, patch: dict[str, Any]) -> dict[str, Any]:
    """Apply a validated partial update and bump the schedule revision."""
    current = get_job(conn, job_id)
    if current is None:
        raise CronRegistryError(f"未知任务: {job_id}")

    hour = patch.get("hour", current["hour"])
    minute = patch.get("minute", current["minute"])
    day_of_week = patch.get("day_of_week", current["day_of_week"])
    kwargs = patch.get("func_kwargs", current["func_kwargs"])
    is_active = patch.get("is_active", current["is_active"])

    if not isinstance(hour, int) or not 0 <= hour <= 23:
        raise CronRegistryError("hour 需为 0-23 的整数")
    if not isinstance(minute, int) or not 0 <= minute <= 59:
        raise CronRegistryError("minute 需为 0-59 的整数")
    parse_days(str(day_of_week))
    if not isinstance(kwargs, dict):
        raise CronRegistryError("func_kwargs 需为对象")

    now = datetime.now(CST).isoformat()
    conn.execute(
        """
        UPDATE cron_jobs
           SET hour = ?, minute = ?, day_of_week = ?, func_kwargs = ?,
               is_active = ?, updated_at = ?
         WHERE id = ?
        """,
        (hour, minute, str(day_of_week), json.dumps(kwargs, ensure_ascii=False),
         1 if is_active else 0, now, job_id),
    )
    conn.commit()
    bump_revision(conn)
    updated = get_job(conn, job_id)
    assert updated is not None
    return updated


def bump_revision(conn: sqlite3.Connection) -> int:
    conn.execute(
        """
        INSERT INTO cron_meta (key, value) VALUES (?, '1')
        ON CONFLICT(key) DO UPDATE SET value = CAST(CAST(value AS INTEGER) + 1 AS TEXT)
        """,
        (_META_REVISION,),
    )
    conn.commit()
    return get_revision(conn)


def get_revision(conn: sqlite3.Connection) -> int:
    row = conn.execute("SELECT value FROM cron_meta WHERE key = ?", (_META_REVISION,)).fetchone()
    try:
        return int(row["value"]) if row else 0
    except (TypeError, ValueError):
        return 0


def write_heartbeat(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        INSERT INTO cron_meta (key, value) VALUES (?, ?)
        ON CONFLICT(key) DO UPDATE SET value = excluded.value
        """,
        (_META_HEARTBEAT, datetime.now(CST).isoformat()),
    )
    conn.commit()


def heartbeat_age_sec(conn: sqlite3.Connection) -> float | None:
    row = conn.execute("SELECT value FROM cron_meta WHERE key = ?", (_META_HEARTBEAT,)).fetchone()
    if not row or not row["value"]:
        return None
    try:
        last = datetime.fromisoformat(row["value"])
    except ValueError:
        return None
    if last.tzinfo is None:
        last = last.replace(tzinfo=CST)
    return round((datetime.now(CST) - last).total_seconds(), 1)


def enqueue_run(conn: sqlite3.Connection, job_id: str) -> int:
    """Queue an immediate run; raises :class:`AlreadyQueued` if one is outstanding."""
    if get_job(conn, job_id) is None:
        raise CronRegistryError(f"未知任务: {job_id}")
    row = conn.execute(
        "SELECT id FROM cron_run_requests WHERE job_id = ? AND status IN ('pending','claimed') LIMIT 1",
        (job_id,),
    ).fetchone()
    if row is not None:
        raise AlreadyQueued(f"任务 {job_id} 已在队列中或正在运行")
    now = datetime.now(CST).isoformat()
    cursor = conn.execute(
        "INSERT INTO cron_run_requests (job_id, requested_at, status) VALUES (?, ?, 'pending')",
        (job_id, now),
    )
    conn.commit()
    return int(cursor.lastrowid)


def claim_pending_requests(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Claim up to 5 pending requests for execution."""
    rows = conn.execute(
        "SELECT id, job_id FROM cron_run_requests WHERE status = 'pending' ORDER BY id LIMIT 5"
    ).fetchall()
    claimed: list[dict[str, Any]] = []
    now = datetime.now(CST).isoformat()
    for row in rows:
        cursor = conn.execute(
            "UPDATE cron_run_requests SET status = 'claimed', claimed_at = ? WHERE id = ? AND status = 'pending'",
            (now, row["id"]),
        )
        if cursor.rowcount:
            claimed.append({"id": int(row["id"]), "job_id": row["job_id"]})
    conn.commit()
    return claimed


def release_stale_claims(conn: sqlite3.Connection, *, reason: str) -> list[int]:
    """Fail every run request still marked ``claimed``.

    Called once when a scheduler process starts. Only one scheduler may run
    (launchd KeepAlive), so a ``claimed`` row at startup belongs to a process
    that died mid-run; without this, ``enqueue_run`` answers 409 forever.
    """
    rows = conn.execute(
        "SELECT id FROM cron_run_requests WHERE status = 'claimed' ORDER BY id"
    ).fetchall()
    if not rows:
        return []
    conn.execute(
        "UPDATE cron_run_requests SET status = 'failed', finished_at = ?, error = ? "
        "WHERE status = 'claimed'",
        (datetime.now(CST).isoformat(), reason),
    )
    conn.commit()
    return [int(row["id"]) for row in rows]


def finish_request(conn: sqlite3.Connection, request_id: int, *, error: str | None = None) -> None:
    conn.execute(
        "UPDATE cron_run_requests SET status = ?, finished_at = ?, error = ? WHERE id = ?",
        ("failed" if error else "done", datetime.now(CST).isoformat(), error, request_id),
    )
    conn.commit()


def validate_func_path(func_path: str) -> None:
    """Only allow job functions that live in ``moni.cron.jobs`` and exist."""
    if not func_path.startswith("moni.cron.jobs:"):
        raise CronRegistryError("func_path 必须位于 moni.cron.jobs")
    from . import jobs as jobs_module

    func_name = func_path.split(":", 1)[1]
    if not callable(getattr(jobs_module, func_name, None)):
        raise CronRegistryError(f"未找到任务函数: {func_name}")
