"""Service control endpoints — status, restart, stop."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter

router = APIRouter(prefix="/api/service", tags=["service"])

REPO_ROOT = Path(__file__).resolve().parents[3]
PIDFILE = REPO_ROOT / "data" / ".web.pid"
WEB_SH = REPO_ROOT / "scripts" / "web.sh"
SERVICE_PORT = 8900


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
