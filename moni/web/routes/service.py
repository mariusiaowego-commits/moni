"""Service control endpoints — status, restart, stop."""

from __future__ import annotations

import os
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Query

router = APIRouter(prefix="/api/service", tags=["service"])

REPO_ROOT = Path(__file__).resolve().parents[3]
PIDFILE = REPO_ROOT / "data" / ".web.pid"
WEB_SH = REPO_ROOT / "scripts" / "web.sh"
SERVICE_PORT = 8900

# The admin panel hovers this endpoint.  Shelling out to git on every hover
# would be wasteful, so cache the whole payload briefly.
_INFO_TTL_SEC = 30.0
_info_cache: dict[str, Any] = {}
_info_cached_at = 0.0


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


def _read_pidfile() -> int | None:
    """Read PID from pidfile if present and process is alive."""
    try:
        pid = int(PIDFILE.read_text().strip())
    except (ValueError, OSError, FileNotFoundError):
        return None
    return pid if _pid_alive(pid) else None


def _find_listener_via_lsof(port: int) -> int | None:
    """Use lsof (works for own processes on macOS without elevated rights)."""
    try:
        out = subprocess.check_output(
            ["lsof", f"-iTCP:{port}", "-sTCP:LISTEN", "-n", "-P", "-t"],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=2,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    for line in out.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            pid = int(line)
        except ValueError:
            continue
        if _pid_alive(pid):
            return pid
    return None


def _find_listener_via_psutil(port: int) -> int | None:
    """psutil path — may AccessDenied on macOS for other users' sockets."""
    try:
        import psutil
    except ImportError:
        return None
    try:
        # AccessDenied is common on macOS without Full Disk Access; must not 500.
        conns = psutil.net_connections(kind="inet")
    except Exception:
        return None
    for conn in conns:
        try:
            if conn.status != psutil.CONN_LISTEN:
                continue
            if conn.laddr and conn.laddr.port == port and conn.pid:
                if _pid_alive(conn.pid):
                    return int(conn.pid)
        except Exception:
            continue
    return None


def _find_listener_pid(port: int = SERVICE_PORT) -> int | None:
    """Find a process listening on *port* (fallback when pidfile is stale).

    Prefer ``lsof`` first (reliable on macOS for the moni owner process),
    then psutil when available.
    """
    pid = _find_listener_via_lsof(port)
    if pid is not None:
        return pid
    return _find_listener_via_psutil(port)


def _heal_pidfile(pid: int) -> None:
    """Rewrite pidfile so web.sh stop/status stay consistent after manual starts."""
    try:
        PIDFILE.parent.mkdir(parents=True, exist_ok=True)
        PIDFILE.write_text(f"{pid}\n")
    except OSError:
        pass


def resolve_service_pid(port: int = SERVICE_PORT, heal: bool = True) -> tuple[int | None, str]:
    """Resolve moni web PID.

    Returns (pid, source) where source is ``pidfile`` | ``port`` | ``none``.
    When only the port listener is found, optionally rewrite the pidfile.
    """
    pid = _read_pidfile()
    if pid is not None:
        return pid, "pidfile"

    pid = _find_listener_pid(port)
    if pid is not None:
        if heal:
            _heal_pidfile(pid)
        return pid, "port"

    return None, "none"


def _uptime(pid: int | None) -> float | None:
    """Return process uptime in seconds, or None if unavailable."""
    if pid is None:
        return None
    try:
        import psutil

        return time.time() - psutil.Process(pid).create_time()
    except (ImportError, OSError):
        try:
            return time.time() - PIDFILE.stat().st_mtime
        except OSError:
            return None


def _git(*args: str) -> str | None:
    """Run a read-only git command in the repo root; None when unavailable."""
    try:
        out = subprocess.check_output(
            ["git", *args],
            cwd=REPO_ROOT,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=3,
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, FileNotFoundError, OSError):
        return None
    return out.strip() or None


_DESCRIBE_RE = re.compile(r"^(?P<tag>.+?)-(?P<ahead>\d+)-g(?P<commit>[0-9a-f]+)$")


def _code_info() -> dict[str, Any]:
    """Version, branch and worktree state — all optional, git may be absent."""
    describe = _git("describe", "--tags", "--always")
    version = None
    ahead = None
    commit = None
    if describe:
        match = _DESCRIBE_RE.match(describe)
        if match:
            version = match.group("tag")
            ahead = int(match.group("ahead"))
            commit = match.group("commit")
        elif describe.startswith("v"):
            version = describe
            ahead = 0
            commit = _git("rev-parse", "--short", "HEAD")
        else:
            commit = describe

    branch = _git("rev-parse", "--abbrev-ref", "HEAD")
    if branch == "HEAD":
        branch = None
    dirty_out = _git("status", "--porcelain")
    return {
        "version": version,
        "commit": commit,
        "commits_ahead": ahead,
        "branch": branch,
        "worktree_dirty": None if dirty_out is None else bool(dirty_out),
    }


def _data_info() -> dict[str, Any]:
    """Schema version, database file, article count and queue depth."""
    from moni.db.connection import get_db, get_db_path

    info: dict[str, Any] = {
        "schema_version": None,
        "db_name": None,
        "db_size_mb": None,
        "articles": None,
        "jobs_pending": None,
        "jobs_running": None,
        "llm_mode": None,
        "last_fetch_at": None,
    }
    try:
        conn = get_db()
    except Exception:  # noqa: BLE001 — panel must never break the page
        return info

    try:
        info["schema_version"] = int(
            conn.execute("SELECT COALESCE(MAX(version), 0) FROM schema_version").fetchone()[0]
        )
    except Exception:  # noqa: BLE001
        pass
    try:
        info["articles"] = int(conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0])
    except Exception:  # noqa: BLE001
        pass
    try:
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM llm_jobs "
            "WHERE status IN ('pending', 'running') GROUP BY status"
        ).fetchall()
        counts = {row["status"]: int(row["n"]) for row in rows}
        info["jobs_pending"] = counts.get("pending", 0)
        info["jobs_running"] = counts.get("running", 0)
    except Exception:  # noqa: BLE001
        pass
    try:
        row = conn.execute(
            "SELECT value FROM llm_settings WHERE key = 'llm_model_mode'"
        ).fetchone()
        info["llm_mode"] = row["value"] if row else None
    except Exception:  # noqa: BLE001
        pass
    try:
        info["last_fetch_at"] = conn.execute(
            "SELECT MAX(fetched_at) FROM articles"
        ).fetchone()[0]
    except Exception:  # noqa: BLE001
        pass

    try:
        path = Path(get_db_path())
        # Only the file name: the panel is a UI surface, not a shell.
        info["db_name"] = path.name
        info["db_size_mb"] = round(path.stat().st_size / (1024 * 1024), 1)
    except Exception:  # noqa: BLE001
        pass
    return info


@router.get("/info")
async def service_info(force: bool = Query(False)) -> dict[str, Any]:
    """Aggregated admin snapshot for the top-right hover panel.

    Every field degrades to ``null`` rather than raising: the panel is a
    convenience surface, not a health check.
    """
    global _info_cached_at

    now = time.time()
    if not force and _info_cache and now - _info_cached_at < _INFO_TTL_SEC:
        return _info_cache

    pid, source = resolve_service_pid(SERVICE_PORT, heal=True)
    running = pid is not None
    uptime_sec = _uptime(pid) if running else None
    payload: dict[str, Any] = {
        "running": running,
        "pid": pid,
        "port": SERVICE_PORT,
        "uptime_sec": round(uptime_sec, 1) if uptime_sec else None,
        "source": source,
    }
    payload.update(_code_info())
    payload.update(_data_info())

    _info_cache.clear()
    _info_cache.update(payload)
    _info_cached_at = now
    return payload


@router.get("/status")
async def service_status():
    """Return service status: running, pid, uptime.

    Primary source is ``data/.web.pid`` (written by ``scripts/web.sh start``).
    If that PID is missing/stale but something still listens on the service
    port (e.g. agent started uvicorn without writing the pidfile), report
    that process as running and heal the pidfile.
    """
    pid, source = resolve_service_pid(SERVICE_PORT, heal=True)
    running = pid is not None
    uptime_sec = _uptime(pid) if running else None
    return {
        "running": running,
        "pid": pid,
        "uptime_sec": round(uptime_sec, 1) if uptime_sec else None,
        "port": SERVICE_PORT,
        "source": source,
    }


@router.post("/restart")
async def service_restart():
    """Restart the web service.

    Returns 200 immediately, then spawns a background process that
    stops and starts the service after a short delay.
    """
    # Ensure pidfile points at the live listener so web.sh stop can kill it.
    resolve_service_pid(SERVICE_PORT, heal=True)
    subprocess.Popen(
        ["bash", "-c", f"sleep 1 && bash {WEB_SH} stop && sleep 1 && bash {WEB_SH} start"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "message": "Restarting... service will be back in ~3 seconds."}


@router.post("/stop")
async def service_stop():
    """Stop the web service.

    Returns 200 immediately, then spawns a background process that
    stops the service after a short delay.
    """
    resolve_service_pid(SERVICE_PORT, heal=True)
    subprocess.Popen(
        ["bash", "-c", f"sleep 1 && bash {WEB_SH} stop"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "message": "Stopping... service will shut down in ~1 second."}
