"""moni.cron — apscheduler-based job scheduler.

Public API::

    from moni.cron import CronScheduler, run_history

    scheduler = CronScheduler()
    scheduler.start()      # start BackgroundScheduler
    scheduler.stop()       # graceful shutdown
    scheduler.status()     # dict with running state

    # Log a completed run
    run_history.log_run(job_id="nightly_fetch", success=True, error=None)
"""

from __future__ import annotations

from .run_history import CronRunHistory
from .scheduler import CST, CronScheduler

# Singleton instance — lazily created by CLI, imported by callers
run_history = CronRunHistory()

__all__ = ["CronScheduler", "CronRunHistory", "run_history", "CST"]
