"""moni.share.worker — start the default_worker.sh subprocess on demand.

v1.0.5.17: UI button "立即处理队列" triggers POST /api/share/worker/run
which spawns default_worker.sh as a detached subprocess and returns
202 + pid immediately.  The web 8900 process stays responsive (Popen
doesn't block); the worker scans ~/.moni/queue/*.json, processes
pending share_tasks, and writes progress updates to share_tasks.progress_*
columns (see migration 008).

Single-instance: default_worker.sh already has a PID-file lock in
$QUEUE_DIR/.lock.  We double-check here so the UI can tell the user
"already running" without spawning a redundant subprocess.

Log path: $MONI_OUTPUT_DIR/worker.log if set, else
data/artifacts/worker.log (alongside the artifacts).  The frontend
tails the last N lines via GET /api/share/worker/log?tail=N.
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

# Resolve to the same DB / queue / output paths as the rest of the
# moni web layer.  The CLI workflow sets MONI_DB / MONI_QUEUE_DIR
# in the env; we honour those if set, otherwise fall back to the
# project-relative defaults.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "news.db"
DEFAULT_QUEUE_DIR = Path(os.environ.get("HOME", "~")) / ".moni" / "queue"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "artifacts"
WORKER_SCRIPT = REPO_ROOT / "scripts" / "default_worker.sh"
LOCK_FILE = DEFAULT_QUEUE_DIR / ".lock"
LOG_FILE = DEFAULT_OUTPUT_DIR / "worker.log"


@dataclass
class WorkerLaunch:
    """Result of trying to start a worker.  Either launched (pid > 0)
    or already_running (pid > 0 of the existing one) or failed."""

    started: bool          # True if we just spawned a new worker
    already_running: bool  # True if another worker was already up
    pid: int | None        # worker PID (new or existing)
    log_path: str          # path to worker.log
    error: str | None = None  # populated when started=False and not already_running


def _check_lock() -> int | None:
    """Return PID of the worker holding the lock, or None if free.

    Mirrors the POSIX lock-file logic in default_worker.sh so we
    give the UI the same "is it running?" answer the worker itself
    would see.
    """
    try:
        if not LOCK_FILE.exists():
            return None
        text = LOCK_FILE.read_text().strip()
        pid = int(text)
        # kill -0 returns 0 if the process is alive (we can signal
        # it without actually sending a signal).
        try:
            os.kill(pid, 0)
            return pid
        except OSError:
            # Stale lock — worker is dead but didn't clean up.  We
            # treat this as "not running" so the UI can launch a
            # fresh worker; the new worker's PID-file create will
            # overwrite the stale one.
            return None
    except (ValueError, OSError):
        return None


def launch_worker() -> WorkerLaunch:
    """Spawn default_worker.sh as a detached subprocess.

    Returns a WorkerLaunch describing what happened:
    - started=True, pid=N:  we just spawned it
    - already_running=True, pid=N: another worker is already up
    - started=False, error='...': failed (e.g. worker.sh not
      executable, DB path missing).  The UI surfaces the error.
    """
    # 1. Single-instance check
    existing = _check_lock()
    if existing is not None:
        return WorkerLaunch(
            started=False,
            already_running=True,
            pid=existing,
            log_path=str(LOG_FILE),
        )

    # 2. Pre-flight: worker.sh must be executable
    if not WORKER_SCRIPT.is_file():
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(LOG_FILE),
            error=f"worker script not found: {WORKER_SCRIPT}",
        )

    # 3. Pre-flight: DB path parent must exist
    db_path = Path(os.environ.get("MONI_DB", str(DEFAULT_DB)))
    if not db_path.parent.exists():
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(LOG_FILE),
            error=f"DB parent dir missing: {db_path.parent}",
        )

    # 4. Ensure log dir exists + log file is created (so the GET
    # /api/share/worker/log endpoint has something to read even
    # before the worker has flushed its first line).
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
    LOG_FILE.touch(exist_ok=True)

    # 5. Build env.  We forward PATH (hermes CLI is on PATH) and
    # the same MONI_DB / MONI_QUEUE_DIR the web layer uses.  The
    # worker has its own defaults that fall back to project-root
    # / HOME, so omitting these would still work in 99% of cases
    # — but we set them explicitly so a future env-tweak to the
    # web layer doesn't silently break the worker.
    env = os.environ.copy()
    env.setdefault("MONI_DB", str(db_path))
    env.setdefault("MONI_QUEUE_DIR", str(DEFAULT_QUEUE_DIR))
    env.setdefault("MONI_OUTPUT_DIR", str(DEFAULT_OUTPUT_DIR))

    # 6. Spawn detached.  start_new_session puts the worker in its
    # own process group so SIGTERM on 8900 doesn't kill it (the
    # user explicitly wants the worker to outlive the request that
    # triggered it — they may close the browser tab while it's
    # still running).  stdout/stderr both go to the same log file
    # so a tail view shows interleaved lines.
    try:
        log_fh = open(LOG_FILE, "a", encoding="utf-8")
    except OSError as exc:
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(LOG_FILE),
            error=f"cannot open log file: {exc!r}",
        )
    try:
        proc = subprocess.Popen(
            ["/bin/bash", str(WORKER_SCRIPT)],
            cwd=str(REPO_ROOT),
            env=env,
            stdout=log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        log_fh.close()
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(LOG_FILE),
            error=f"Popen failed: {exc!r}",
        )
    # Don't close log_fh — the worker inherited the fd; closing
    # the Python wrapper would just decref but the OS keeps the
    # fd open until the worker exits.  Python's GC handles it.

    log.info("worker spawned: pid=%d log=%s", proc.pid, LOG_FILE)
    return WorkerLaunch(
        started=True, already_running=False, pid=proc.pid,
        log_path=str(LOG_FILE),
    )


def tail_log(n: int = 200) -> str:
    """Return the last ``n`` lines of worker.log (or '' if missing)."""
    if not LOG_FILE.is_file():
        return ""
    try:
        # Use a deque to keep memory bounded for huge log files.
        from collections import deque
        with open(LOG_FILE, "r", encoding="utf-8", errors="replace") as fh:
            return "".join(deque(fh, maxlen=n))
    except OSError as exc:
        return f"(error reading log: {exc!r})"
