"""scheduler — apscheduler-based BackgroundScheduler for moni cron jobs.

Design (v1.0):
- Runs as a SEPARATE process from uvicorn/web; communicates via SQLite DB.
- Scheduler state is in-process only — crash = no automatic reschedule.
- CST timezone (Asia/Shanghai).
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

# CST = UTC+8 (Asia/Shanghai, use ZoneInfo for apscheduler compat)
CST = ZoneInfo("Asia/Shanghai")

# Path to schedule config, relative to the moni package root
_CONFIG_PATH = Path(__file__).parent.parent.parent / "config" / "schedule.yaml"

# Singleton state — lives in the scheduler PROCESS, not shared with web process
_scheduler: Optional[Any] = None  # apscheduler BackgroundScheduler

# A live job never runs this long (bestblogs full sweep ≈ 20 min); rows still
# marked in_progress past this are leftovers of a failed final UPDATE.
_STALE_RUN_SEC = 3 * 3600


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
        self._explicit_config = config_path is not None
        self._scheduler: Optional[Any] = None
        self._jobs: list[dict[str, Any]] = []
        self._revision = 0

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

        if not self._explicit_config:
            self._recover_interrupted_state()
            # Registry tick: heartbeat + hot reload + run-request consumer.
            self._scheduler.add_job(
                self._tick,
                "interval",
                seconds=15,
                id="_cron_tick",
                replace_existing=True,
            )
            self._heartbeat()

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

    # ── registry / run-request loop ───────────────────────────────────────────

    def _recover_interrupted_state(self) -> None:
        """Close state left behind by a scheduler process that died mid-run."""
        from moni.db.connection import get_db

        from . import registry
        from .run_history import mark_interrupted_runs

        try:
            conn = get_db()
            orphaned = registry.release_stale_claims(
                conn, reason="调度器重启：上一次手动触发未完成"
            )
            if orphaned:
                logger.warning("Recovered orphaned run request(s): %s", orphaned)
            interrupted = mark_interrupted_runs(conn)
            if interrupted:
                logger.warning("Marked interrupted cron run(s) as failed: %s", interrupted)
        except Exception:
            # Recovery is best-effort: never let it stop the scheduler, or
            # launchd KeepAlive would restart us into a crash loop.
            logger.exception("cron startup recovery failed — continuing")

    def _heartbeat(self) -> None:
        from moni.db.connection import get_db

        from . import registry

        registry.write_heartbeat(get_db())

    def _tick(self) -> None:
        """15s loop: heartbeat, hot reload on revision change, consume run requests."""
        from moni.db.connection import get_db

        from . import registry
        from .run_history import mark_interrupted_runs

        conn = get_db()
        try:
            registry.write_heartbeat(conn)
            stale = mark_interrupted_runs(conn, older_than_sec=_STALE_RUN_SEC)
            if stale:
                logger.warning("Closed stale in_progress cron run(s): %s", stale)
            if registry.get_revision(conn) != self._revision:
                self._reload_jobs()
            for request in registry.claim_pending_requests(conn):
                job = registry.get_job(conn, request["job_id"])
                if job is None:
                    registry.finish_request(conn, request["id"], error="任务不存在")
                    continue
                assert self._scheduler is not None
                self._scheduler.add_job(
                    self._execute_request,
                    "date",
                    args=[request["id"], job["func_path"], job["func_kwargs"]],
                    id=f"_run_req_{request['id']}",
                    replace_existing=True,
                )
        except Exception:  # a broken tick must not kill the scheduler thread
            logger.exception("cron tick failed")

    def _execute_request(self, request_id: int, func_path: str, func_kwargs: dict[str, Any]) -> None:
        from moni.db.connection import get_db

        from . import registry

        conn = get_db()
        try:
            _resolve_func(func_path)(**func_kwargs)
        except Exception as exc:
            logger.exception("run request %s failed", request_id)
            registry.finish_request(conn, request_id, error=str(exc)[:500])
        else:
            registry.finish_request(conn, request_id)

    def _reload_jobs(self) -> None:
        from moni.db.connection import get_db

        from . import registry

        assert self._scheduler is not None
        for job in self._scheduler.get_jobs():
            if job.id != "_cron_tick":
                self._scheduler.remove_job(job.id)
        self._jobs = []

        conn = get_db()
        jobs = registry.list_jobs(conn, active_only=True)
        for cfg in jobs:
            if cfg["trigger_type"] == "cron":
                self._register_job(cfg)
        self._revision = registry.get_revision(conn)
        logger.info("Reloaded %d job(s) at revision %d", len(jobs), self._revision)

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
        """Load job definitions.

        Production (default path): read the ``cron_jobs`` registry and seed it
        from YAML when empty.  Explicit ``config_path`` (tests, ad-hoc runs):
        keep the legacy YAML-only behaviour so fixtures stay hermetic.
        """
        if self._explicit_config:
            if not self.config_path.exists():
                logger.warning("schedule.yaml not found at %s", self.config_path)
                return
            data = yaml.safe_load(self.config_path.read_text(encoding="utf-8")) or {}
            jobs = data.get("jobs", []) if isinstance(data, dict) else []
            for job_cfg in jobs:
                self._register_job(job_cfg)
            logger.debug("Loaded %d job(s) from %s", len(jobs), self.config_path)
            return

        from moni.db.connection import get_db

        from . import registry

        conn = get_db()
        registry.seed_from_yaml(conn, self.config_path)
        jobs = registry.list_jobs(conn, active_only=True)
        for job_cfg in jobs:
            if job_cfg["trigger_type"] == "cron":
                self._register_job(job_cfg)
        self._revision = registry.get_revision(conn)
        logger.info(
            "Loaded %d job(s) from cron_jobs (revision %d)", len(jobs), self._revision
        )

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
