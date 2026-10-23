"""moni.share.worker — start the default_worker.sh subprocess on demand.

v1.0.5.17: UI button "立即处理队列" triggers POST /api/share/worker/run
which spawns default_worker.sh as a detached subprocess and returns
202 + pid immediately.  The web 8900 process stays responsive (Popen
doesn't block); the worker scans ~/.moni/queue/*.json, processes
pending share_tasks, and writes progress updates to share_tasks.progress_*
columns (see migration 008).

Single-instance: default_worker.sh takes a PID-file lock in
$QUEUE_DIR/.lock.  We double-check here so the UI can tell the user
"already running" without spawning a redundant subprocess.

v1.0.48: the bash side now creates that lock **atomically**
(``set -o noclobber`` → O_EXCL) instead of check-then-write, and this
side waits for the freshly spawned child to actually claim the lock
before reporting "started".  Spawning alone never meant success — the
loser of the race exits immediately — and the old code reported a PID
for a worker that was already gone.

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

from moni.share.manifest import resolve_queue_dir

log = logging.getLogger(__name__)

# Resolve to the same DB / queue / output paths as the rest of the
# moni web layer.  The CLI workflow sets MONI_DB / MONI_QUEUE_DIR
# in the env; we honour those if set, otherwise fall back to the
# project-relative defaults.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = REPO_ROOT / "data" / "news.db"
DEFAULT_OUTPUT_DIR = REPO_ROOT / "data" / "artifacts"
WORKER_SCRIPT = REPO_ROOT / "scripts" / "default_worker.sh"
LOG_FILE = DEFAULT_OUTPUT_DIR / "worker.log"

#: How long to wait for a freshly spawned worker to prove it owns the lock.
#: The worker has to boot bash, source .env and reach the lock block, so
#: this is generous; it is only paid when a launch actually happens.
_LOCK_SETTLE_SECONDS = 3.0


def _queue_dir() -> Path:
    """Resolve the share-queue directory.

    v1.0.48: honour ``MONI_QUEUE_DIR`` — the CLI workflow exports it and
    ``default_worker.sh`` resolves it the same way
    (``${MONI_QUEUE_DIR:-${HOME:-~}/.moni/queue}``).  The Python side used
    to force the ``$HOME``-based default onto the child process, silently
    discarding the caller's choice even though the module docstring
    claimed it was honoured.
    """
    return resolve_queue_dir()


def _lock_file() -> Path:
    """Path of the worker PID lock (same file the bash script writes)."""
    return _queue_dir() / ".lock"


@dataclass
class WorkerLaunch:
    """Result of trying to start a worker.  Either launched (pid > 0)
    or already_running (pid > 0 of the existing one) or failed."""

    started: bool          # True if we just spawned a new worker
    already_running: bool  # True if another worker was already up
    pid: int | None        # worker PID (new or existing)
    log_path: str          # path to worker.log
    error: str | None = None  # populated when started=False and not already_running
    #: True when the child exited 0 without ever taking the lock — i.e.
    #: there was nothing for it to do (empty queue) or the queue dir was
    #: missing.  Distinct from a failure: the UI must not say "启动失败".
    completed_nothing_to_do: bool = False


def _pid_alive(pid: int) -> bool:
    """True if the process exists (signal 0 does not deliver anything)."""
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_lock_pid() -> int | None:
    """PID recorded in the lock file, or None if absent / unreadable."""
    try:
        return int(_lock_file().read_text().strip())
    except (ValueError, OSError):
        return None


def _check_lock() -> int | None:
    """Return PID of the worker holding the lock, or None if free.

    Mirrors the POSIX lock-file logic in default_worker.sh so we
    give the UI the same "is it running?" answer the worker itself
    would see.
    """
    pid = _read_lock_pid()
    if pid is None:
        return None
    if not _pid_alive(pid):
        # Stale lock — worker is dead but didn't clean up.  We treat
        # this as "not running" so the UI can launch a fresh worker;
        # the new worker's atomic PID-file create reclaims it.
        return None
    return pid


@dataclass
class _LockOutcome:
    """Result of waiting for a freshly spawned worker to claim the lock."""

    kind: str                 # "won" | "lost" | "exited" | "timeout"
    holder_pid: int | None = None   # who holds it (kind == "lost")
    returncode: int | None = None   # child exit code (kind == "exited")


def _await_lock_outcome(proc: subprocess.Popen) -> _LockOutcome:
    """Wait until the spawned worker owns the lock — or gives up.

    v1.0.48: the bash worker takes ``$QUEUE_DIR/.lock`` itself and exits
    immediately when another worker already holds it.  So spawning is
    *not* proof of success: two concurrent requests can both spawn, one
    of them exits right away.  Polling the lock file tells us which one
    actually got the work, and lets the UI report the right PID.
    """
    deadline = time.monotonic() + _LOCK_SETTLE_SECONDS
    while True:
        holder = _read_lock_pid()
        if holder is not None:
            if holder == proc.pid:
                return _LockOutcome("won")
            if _pid_alive(holder):
                return _LockOutcome("lost", holder_pid=holder)
        rc = proc.poll()
        if rc is not None:
            return _LockOutcome("exited", returncode=rc)
        if time.monotonic() >= deadline:
            return _LockOutcome("timeout")
        time.sleep(0.05)


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

    # 1b. Pre-flight: queue directory must exist (audit F4)
    # 如果队列目录不存在, 脚本会立即 exit 0, 但那不是"队列已处理完",
    # 而是环境配置错误或未初始化。在此拦截并返回明确错误,
    # 避免误报成 completed_nothing_to_do=True 欺骗前端。
    q_dir = _queue_dir()
    if not q_dir.exists():
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(LOG_FILE),
            error=f"queue dir not found: {q_dir}",
        )

    # 2. Pre-flight: worker.sh must be executable
    if not WORKER_SCRIPT.is_file():
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(LOG_FILE),
            error=f"worker script not found: {WORKER_SCRIPT}",
        )

    # 3. Pre-flight: DB path.  v1.0.9.3 fix: also handle the case
    # where env has MONI_DB='' (empty string) — Python's setdefault
    # only checks key-presence, not falsy, so an empty MONI_DB in
    # the web's env would silently leave the worker reading the
    # WRONG default ~/.moni/data/moni.db.  We always set the
    # project-relative path explicitly; if the caller wants a
    # different DB they can pass it via the env var to override
    # by setting a non-empty value at 8900 launch time.
    db_path = Path(os.environ.get("MONI_DB") or str(DEFAULT_DB))
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
    # the same MONI_DB / MONI_QUEUE_DIR the web layer uses.  We
    # use direct assignment (not setdefault) for MONI_DB so an
    # empty-string value in the caller's env doesn't leak through.
    env = os.environ.copy()
    env["MONI_DB"] = str(db_path)
    env["MONI_QUEUE_DIR"] = str(_queue_dir())
    env["MONI_OUTPUT_DIR"] = str(DEFAULT_OUTPUT_DIR)

    # v1.0.5.26: defense-in-depth PATH prepend.  default_worker.sh
    # also does this on its own, but the worker Popen here might
    # also run code paths that exec hermes directly.  Adding
    # ~/.local/bin to the front works whether the launch came from
    # the web UI button, cron, launchd, or a manual shell — all of
    # these have different default PATHs and only some include
    # the directory where `hermes` lives.
    #
    # v1.0.9.3: ALSO prepend /usr/local/bin so the worker's
    # `python3` (3.12) has the project deps (yaml, etc.).  macOS
    # system python3 (3.14) is missing yaml and breaks
    # `moni.fetch.bestblogs_api` import — the worker silently dies
    # with ModuleNotFoundError and the share task gets stuck
    # forever.  /usr/local/bin/python3 is the same interpreter
    # the v1.0.8 web.sh fix pinned, matching the web process.
    env["PATH"] = (
        f"{Path.home()}/.local/bin:/usr/local/bin:" + env.get("PATH", "")
    )

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

    # ── Did this child actually get the lock? (v1.0.48) ──────────────────
    # Spawning is not success: default_worker.sh takes the same lock and
    # exits at once if another worker already holds it.  Two concurrent
    # requests both spawn; exactly one survives.  Ask the lock file who
    # won instead of guessing, so the UI reports the real PID.
    outcome = _await_lock_outcome(proc)

    if outcome.kind == "won":
        log.info("worker spawned: pid=%d log=%s", proc.pid, LOG_FILE)
        return WorkerLaunch(
            started=True, already_running=False, pid=proc.pid,
            log_path=str(LOG_FILE),
        )

    if outcome.kind == "lost":
        log.info(
            "worker launch lost the race — pid=%s already holds the lock",
            outcome.holder_pid,
        )
        return WorkerLaunch(
            started=False, already_running=True, pid=outcome.holder_pid,
            log_path=str(LOG_FILE),
        )

    if outcome.kind == "exited" and outcome.returncode == 0:
        # The child finished on its own without ever taking the lock.
        #
        # v1.0.49.1 (audit F4): this used to report ``started=True`` with
        # ``pid=proc.pid`` — a PID that is already reaped by now.  The UI
        # then showed "worker 已启动 (pid=xxx)" for a process that no longer
        # exists, which is exactly the lie the module docstring says this
        # release fixed.  There is no queue work in flight and no live
        # worker, so: not started, not already running, no pid.
        log.info(
            "worker exited cleanly without taking the lock: pid=%d "
            "(nothing to do, or the queue dir is missing)",
            proc.pid,
        )
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(LOG_FILE),
            completed_nothing_to_do=True,
        )

    if outcome.kind == "exited":
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(LOG_FILE),
            error=f"worker exited immediately (rc={outcome.returncode})",
        )

    # The child is still alive (``timeout`` is only returned when
    # ``proc.poll()`` is None).  It may simply be slow to reach the lock
    # block, so reporting "启动失败" here would be a lie and could make
    # the user launch a second worker.  We did start a live process;
    # report that, and let the queue/lock state remain the source of truth.
    log.warning(
        "worker pid=%d is alive but has not claimed the lock after %.1fs; "
        "reporting started and leaving the queue state authoritative",
        proc.pid,
        _LOCK_SETTLE_SECONDS,
    )
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
