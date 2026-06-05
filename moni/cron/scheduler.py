"""scheduler — apscheduler-based BackgroundScheduler for moni cron jobs.

Design (v1.0):
- Runs as a SEPARATE process from uvicorn/web; communicates via SQLite DB.
- Scheduler state is in-process only — crash = no automatic reschedule.
- CST timezone, migrated from source_monitor.py.
- SQLite WAL mode + busy_timeout configured at connection level.
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path
from zoneinfo import ZoneInfo
from typing import Any, Optional

import yaml

logger = logging.getLogger(__name__)

# CST = UTC+8, migrated from source_monitor.py (use ZoneInfo for apscheduler compat)
CST = ZoneInfo("Asia/Shanghai")

# Path to schedule config, relative to the moni package root
_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "schedule.yaml"

# Singleton state — lives in the scheduler PROCESS, not shared with web process
_scheduler: Optional[Any] = None  # apscheduler BackgroundScheduler


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CronScheduler:
    """APScheduler BackgroundScheduler wrapper.

    Loads jobs from ``config/schedule.yaml`` and exposes start/stop/status.

    The scheduler runs in a SEPARATE process from the web (uvicorn) process.
    Communication is via SQLite only.
    """

    def __init__(
        self,
        config_path: Path | str | None = None,
    ) -> None:
        self.config_path = Path(config_path) if config_path else _CONFIG_PATH
        self._scheduler: Optional[Any] = None
        self._jobs: list[dict[str, Any]] = []

    # ── public API ─────────────────────────────────────────────────────────────

    def start(self, background: bool = True) -> None:
        """Start the BackgroundScheduler.

        Args:
            background: If True (default), scheduler runs in background (non-blocking).
                       If False, runs the scheduler in the foreground (for testing).
        """
        if self._scheduler is not None:
            logger.warning("Scheduler already running — call stop() first")
            return

        from apscheduler.schedulers.background import BackgroundScheduler

        self._scheduler = BackgroundScheduler(
            timezone=CST,
            job_defaults={
                "coalesce": True,
                "max_instances": 1,
                "misfire_grace_time": 60,
            },
        )

        self._load_jobs()
        self._scheduler.start()

        # Handle SIGTERM gracefully (docker / systemd)
        def _sigterm_handler(signum, frame):
            logger.info("Received SIGTERM — shutting down scheduler")
            self.stop()
            sys.exit(0)

        signal.signal(signal.SIGTERM, _sigterm_handler)

        logger.info(
            "Scheduler started with %d job(s) at %s",
            len(self._jobs),
            datetime.now(CST).isoformat(),
        )

    def stop(self) -> None:
        """Gracefully shut down the scheduler."""
        if self._scheduler is None:
            logger.debug("stop() called but scheduler is not running")
            return

        self._scheduler.shutdown(wait=True)
        self._scheduler = None
        logger.info("Scheduler stopped")

    def status(self) -> dict[str, Any]:
        """Return the current scheduler state as a dict.

        Returns:
            dict with keys: running (bool), jobs (list of job summaries),
            current_time (CST ISO8601).
        """
        if self._scheduler is None:
            return {
                "running": False,
                "jobs": [],
                "current_time": datetime.now(CST).isoformat(),
            }

        job_summaries = []
        for job in self._scheduler.get_jobs():
            next_run = job.next_run_time
            job_summaries.append({
                "id": job.id,
                "name": job.name,
                "next_run": next_run.isoformat() if next_run else None,
            })

        return {
            "running": True,
            "jobs": job_summaries,
            "current_time": datetime.now(CST).isoformat(),
        }

    # ── job loading ───────────────────────────────────────────────────────────

    def _load_jobs(self) -> None:
        """Load and register jobs from config/schedule.yaml."""
        if not self.config_path.exists():
            logger.warning("schedule.yaml not found at %s", self.config_path)
            return

        with self.config_path.open(encoding="utf-8") as fh:
            data = yaml.safe_load(fh)

        jobs: list[dict[str, Any]] = data.get("jobs", []) if isinstance(data, dict) else []

        for job_cfg in jobs:
            self._register_job(job_cfg)

        logger.debug("Loaded %d job(s) from %s", len(jobs), self.config_path)

    def _register_job(self, cfg: dict[str, Any]) -> None:
        """Register a single job with the apscheduler.

        Args:
            cfg: Job config dict from schedule.yaml. Required keys:
                 id, name, trigger_type, func_path.  Optional: args, kwargs, **trigger_args.
        """
        job_id: str = cfg["id"]
        name: str = cfg["name"]
        trigger_type: str = cfg["trigger_type"]
        func_path: str = cfg["func_path"]  # e.g. "moni.fetch:fetch_all"
        func_args: list[Any] = cfg.get("args", [])
        func_kwargs: dict[str, Any] = cfg.get("func_kwargs", {})

        # Resolve func_path → actual callable
        func = _resolve_func(func_path)

        # Build trigger
        trigger = _build_trigger(trigger_type, cfg)

        self._scheduler.add_job(
            partial(func, **func_kwargs) if func_kwargs else func,
            trigger=trigger,
            id=job_id,
            name=name,
            replace_existing=True,
        )

        self._jobs.append(cfg)
        logger.debug("Registered job %r (%s) with trigger %s", job_id, name, trigger_type)


# ── func resolver ──────────────────────────────────────────────────────────

def _resolve_func(func_path: str):
    """Resolve 'module.submodule:name' → callable object.

    Supports both colon-separated (importlib) and dot-separated module paths.
    """
    if ":" in func_path:
        module_path, _, attr_path = func_path.partition(":")
    else:
        module_path, _, attr_path = func_path.rpartition(".")

    import importlib
    mod = importlib.import_module(module_path)
    if attr_path:
        obj = getattr(mod, attr_path)
    else:
        obj = mod
    return obj


# ── trigger builders ─────────────────────────────────────────────────────────

def _build_trigger(trigger_type: str, cfg: dict[str, Any]) -> Any:
    """Build an apscheduler trigger from config."""
    from apscheduler.triggers import cron, date, interval

    if trigger_type == "interval":
        # Build kwargs with only non-None values (apscheduler IntervalTrigger
        # raises on None for interval fields)
        interval_kwargs = {
            k: cfg[k]
            for k in ("seconds", "minutes", "hours", "days", "weeks")
            if cfg.get(k) is not None
        }
        interval_kwargs["start_date"] = cfg.get("start_date")
        interval_kwargs["end_date"] = cfg.get("end_date")
        # Filter out None for start/end too
        interval_kwargs = {k: v for k, v in interval_kwargs.items() if v is not None}
        return interval.IntervalTrigger(**interval_kwargs)

    if trigger_type == "cron":
        return cron.CronTrigger(
            second=cfg.get("second", 0),
            minute=cfg.get("minute", "*"),
            hour=cfg.get("hour", "*"),
            day=cfg.get("day", "*"),
            month=cfg.get("month", "*"),
            day_of_week=cfg.get("day_of_week", "*"),
            start_date=cfg.get("start_date"),
            end_date=cfg.get("end_date"),
        )

    if trigger_type == "date":
        return date.DateTrigger(
            run_date=cfg.get("run_date"),
        )

    raise ValueError(f"Unknown trigger_type: {trigger_type!r}")


# ── module-level singleton facade (for convenience) ─────────────────────────

def _get_scheduler() -> Optional[Any]:
    """Return the module-level scheduler instance (for CLI use)."""
    return _scheduler


def _set_scheduler(s: Any) -> None:
    """Set the module-level scheduler instance (set by CLI entry point)."""
    global _scheduler
    _scheduler = s
