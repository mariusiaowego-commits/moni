"""moni.share.worker — start the default_worker.sh subprocess on demand.

v1.0.5.17: UI button "立即处理队列" triggers POST /api/share/worker/run
which spawns default_worker.sh as a detached subprocess and returns
202 + pid immediately.  The web 8900 process stays responsive (Popen
doesn't block); the worker scans ~/.moni/queue/*.json, processes
pending share_tasks, and writes progress updates to share_tasks.progress_*
columns (see migration 008).

Single-instance: default_worker.sh inherits a kernel ``flock`` on
$QUEUE_DIR/.lock from worker_lock_exec.py.  We probe that flock here
so the UI can tell the user "already running" without spawning a
redundant subprocess.  flock is tied to the open file description and
is released by the kernel when the worker exits.

Spawning alone is not success: the race loser exits with code 75.  The
winner writes ``.lock.result`` after deciding whether the queue has
work, so an empty queue is reported as such instead of as a started
worker.

Log path: $MONI_OUTPUT_DIR/worker.log if set, else
data/artifacts/worker.log (alongside the artifacts).  The frontend
tails the last N lines via GET /api/share/worker/log?tail=N.
"""
from __future__ import annotations

import fcntl
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
def _default_db() -> Path:
    env_data = os.environ.get("MONI_DATA_DIR")
    if env_data:
        return Path(env_data) / "news.db"
    return REPO_ROOT / "data" / "news.db"


def _output_dir() -> Path:
    env_data = os.environ.get("MONI_DATA_DIR")
    if env_data:
        return Path(env_data) / "artifacts"
    return REPO_ROOT / "data" / "artifacts"


def _log_file() -> Path:
    return _output_dir() / "worker.log"


DEFAULT_DB = _default_db()
DEFAULT_OUTPUT_DIR = _output_dir()
WORKER_SCRIPT = REPO_ROOT / "scripts" / "default_worker.sh"
LOG_FILE = _log_file()

#: How long to wait for a freshly spawned worker to finish.  Empty queues
#: normally finish in well under a second, but the launcher must still be
#: bounded when a worker is genuinely processing a long task.
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
    """Path of the worker flock file (PID text is diagnostic only)."""
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


def _read_lock_pid() -> int | None:
    """PID recorded in the lock file, or None if absent / unreadable."""
    try:
        text = _lock_file().read_text().strip()
        return int(text.splitlines()[0]) if text else None
    except (ValueError, OSError):
        return None


def _check_lock() -> int | None:
    """Return PID of the live flock holder, or None if free.

    The PID text is only diagnostic: the kernel flock is authoritative.
    A stale file with a dead PID (or a PID reused by an unrelated process)
    must never make the UI report a worker as running.
    """
    pid = _read_lock_pid()
    if pid is None:
        return None
    path = _lock_file()
    try:
        fd = os.open(path, os.O_RDWR)
    except OSError:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            # Another process owns the flock.  Do not trust PID text as the
            # authority; it may be a reused PID or a stale generation.
            return pid
        else:
            fcntl.flock(fd, fcntl.LOCK_UN)
            return None
    finally:
        os.close(fd)


def _worker_result_file() -> Path:
    return _queue_dir() / ".lock.result"


def _read_worker_result() -> str | None:
    try:
        text = _worker_result_file().read_text().strip()
    except OSError:
        return None
    return text or None


@dataclass
class _LockOutcome:
    """Result of waiting for a freshly spawned worker to claim the lock."""

    kind: str                 # "started" | "empty" | "lost" | "exited" | "timeout"
    holder_pid: int | None = None   # who holds it (kind == "lost")
    returncode: int | None = None   # child exit code (kind == "exited")


def _await_lock_outcome(proc: subprocess.Popen) -> _LockOutcome:
    """Wait for the child to either finish or prove it owns the flock.

    A worker can finish an empty queue in a few milliseconds.  Returning
    "won" merely because the PID appears in the lock file raced with that
    exit and made 19/20 empty launches look started.  The child now writes
    an explicit result marker; a quick exit is authoritative even if it
    briefly held the lock.
    """
    deadline = time.monotonic() + _LOCK_SETTLE_SECONDS
    while True:
        rc = proc.poll()
        if rc is not None:
            # Read the marker before the exit decision: a fast empty run may
            # finish before the first poll, and its explicit result is the
            # authoritative answer.
            if _read_worker_result() == "nothing_to_do":
                return _LockOutcome("empty")
            return _LockOutcome("exited", returncode=rc)
        result = _read_worker_result()
        if result == "nothing_to_do":
            # Wait for this child to release the flock before the caller can
            # launch the next one.  Otherwise 20 back-to-back calls see the
            # previous empty worker still exiting and report already_running.
            try:
                rc = proc.wait(timeout=deadline - time.monotonic())
            except subprocess.TimeoutExpired:
                return _LockOutcome("timeout")
            return _LockOutcome("empty", returncode=rc)
        if result == "started":
            return _LockOutcome("started")
        holder = _check_lock()
        if holder is not None and holder != proc.pid:
            return _LockOutcome("lost", holder_pid=holder)
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
    log_file = _log_file()
    output_dir = _output_dir()
    if existing is not None:
        return WorkerLaunch(
            started=False,
            already_running=True,
            pid=existing,
            log_path=str(log_file),
        )

    # 1b. Pre-flight: queue directory must exist and be a directory (audit F4 + P3-1)
    # 如果队列目录不存在或为普通文件, 脚本会立即 exit 0, 但那不是"队列已处理完",
    # 而是环境配置错误或未初始化。在此拦截并返回明确错误,
    # 避免误报成 completed_nothing_to_do=True 欺骗前端。
    q_dir = _queue_dir()
    if not q_dir.exists():
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(log_file),
            error=f"queue dir not found: {q_dir}",
        )
    if not q_dir.is_dir():
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(log_file),
            error=f"queue path is not a directory: {q_dir}",
        )

    # 2. Pre-flight: worker.sh must be executable
    if not WORKER_SCRIPT.is_file():
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(log_file),
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
            log_path=str(log_file),
            error=f"DB parent dir missing: {db_path.parent}",
        )

    # 4. Ensure log dir exists + log file is created (so the GET
    # /api/share/worker/log endpoint has something to read even
    # before the worker has flushed its first line).
    log_file.parent.mkdir(parents=True, exist_ok=True)
    log_file.touch(exist_ok=True)

    # 5. Build env.  We forward PATH (hermes CLI is on PATH) and
    # the same MONI_DB / MONI_QUEUE_DIR the web layer uses.  We
    # use direct assignment (not setdefault) for MONI_DB so an
    # empty-string value in the caller's env doesn't leak through.
    env = os.environ.copy()
    env["MONI_DB"] = str(db_path)
    env["MONI_QUEUE_DIR"] = str(_queue_dir())
    env["MONI_OUTPUT_DIR"] = str(output_dir)
    env["ARTIFACTS_DIR"] = str(output_dir)
    env["MONI_WORKER_LOG"] = str(log_file)

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
        log_fh = open(log_file, "a", encoding="utf-8")
    except OSError as exc:
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(log_file),
            error=f"cannot open log file: {exc!r}",
        )
    # A result from an earlier run must not leak into this launch.
    _worker_result_file().unlink(missing_ok=True)

    try:
        proc = subprocess.Popen(
            [
                "/usr/local/bin/python3",
                str(REPO_ROOT / "scripts" / "worker_lock_exec.py"),
                str(_lock_file()),
                str(WORKER_SCRIPT),
            ],
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
            log_path=str(log_file),
            error=f"Popen failed: {exc!r}",
        )
    # Don't close log_fh — the worker inherited the fd; closing
    # the Python wrapper would just decref but the OS keeps the
    # fd open until the worker exits.  Python's GC handles it.

    # ── Did this child actually get the lock? ────────────────────────────
    # worker_lock_exec.py does LOCK_EX|LOCK_NB before exec.  A loser exits
    # with 75 before bash starts; a winner records its PID and keeps fd 9
    # across exec.  An empty queue exits with 0 and writes the explicit
    # "nothing_to_do" result marker.
    outcome = _await_lock_outcome(proc)

    if outcome.kind == "started":
        log.info("worker spawned: pid=%d log=%s", proc.pid, log_file)
        return WorkerLaunch(
            started=True, already_running=False, pid=proc.pid,
            log_path=str(log_file),
        )

    if outcome.kind == "empty":
        log.info("worker spawned and found nothing to do: pid=%d", proc.pid)
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(log_file),
            completed_nothing_to_do=True,
        )

    if outcome.kind == "lost":
        log.info(
            "worker launch lost the race — pid=%s already holds the lock",
            outcome.holder_pid,
        )
        return WorkerLaunch(
            started=False, already_running=True, pid=outcome.holder_pid,
            log_path=str(log_file),
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
            log_path=str(log_file),
            completed_nothing_to_do=False,
        )

    if outcome.kind == "exited":
        return WorkerLaunch(
            started=False, already_running=False, pid=None,
            log_path=str(log_file),
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
        log_path=str(log_file),
    )


def tail_log(n: int = 200) -> str:
    """Return the last ``n`` lines of worker.log (or '' if missing)."""
    log_file = _log_file()
    if not log_file.is_file():
        return ""
    try:
        # Use a deque to keep memory bounded for huge log files.
        from collections import deque
        with open(log_file, "r", encoding="utf-8", errors="replace") as fh:
            return "".join(deque(fh, maxlen=n))
    except OSError as exc:
        return f"(error reading log: {exc!r})"
