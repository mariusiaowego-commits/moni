"""Service control endpoints — status, restart, stop."""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

from fastapi import APIRouter
from fastapi.responses import JSONResponse

router = APIRouter(prefix="/api/service", tags=["service"])

REPO_ROOT = Path(__file__).resolve().parents[3]
PIDFILE = REPO_ROOT / "data" / ".web.pid"
WEB_SH = REPO_ROOT / "scripts" / "web.sh"


def _read_pid() -> int | None:
    """Read PID from pidfile, return None if not found or stale."""
    try:
        pid = int(PIDFILE.read_text().strip())
        # Check if process is alive
        os.kill(pid, 0)
        return pid
    except (ValueError, OSError, FileNotFoundError):
        return None


def _uptime() -> float | None:
    """Return process uptime in seconds, or None if not running."""
    pid = _read_pid()
    if pid is None:
        return None
    try:
        import psutil
        return time.time() - psutil.Process(pid).create_time()
    except ImportError:
        # Fallback: read /proc on Linux, or approximate from pidfile mtime
        try:
            return time.time() - PIDFILE.stat().st_mtime
        except OSError:
            return None


@router.get("/status")
async def service_status():
    """Return service status: running, pid, uptime."""
    pid = _read_pid()
    running = pid is not None
    uptime_sec = _uptime() if running else None
    return {
        "running": running,
        "pid": pid,
        "uptime_sec": round(uptime_sec, 1) if uptime_sec else None,
        "port": 8900,
    }


@router.post("/restart")
async def service_restart():
    """Restart the web service.

    Returns 200 immediately, then spawns a background process that
    stops and starts the service after a short delay.
    """
    # Spawn detached restart process
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
    subprocess.Popen(
        ["bash", "-c", f"sleep 1 && bash {WEB_SH} stop"],
        start_new_session=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return {"ok": True, "message": "Stopping... service will shut down in ~1 second."}
