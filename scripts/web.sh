#!/usr/bin/env bash
# scripts/web.sh — uvicorn process manager
#
# Commands:
#   start  — spawn uvicorn in background, write PID to data/.web.pid
#   stop   — kill the PID, remove pidfile
#   status — check if the PID is alive
#
# PID file location is resolved relative to the repo root (where this script lives).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PIDFILE="$REPO_DIR/data/.web.pid"

# Ensure data/ directory exists
mkdir -p "$REPO_DIR/data"

_start() {
    if [ -s "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        echo "Error: already running (PID $(cat "$PIDFILE"))"
        exit 1
    fi

    # Change to repo root so uvicorn finds the module correctly
    cd "$REPO_DIR"

    # Load secrets from .env if present (so WECHAT_RSS_TOKEN etc. reach the
    # uvicorn process without requiring the user to export them manually).
    if [ -f "$REPO_DIR/.env" ]; then
        set -a
        # shellcheck disable=SC1091
        . "$REPO_DIR/.env"
        set +a
    fi

    # v1.0.8: hardcode the 3.12 interpreter so this script works
    # regardless of which `python3` is first in $PATH.  Homebrew's
    # 3.14 lacks the `yaml` module, which causes the routes package
    # to fail import — all API endpoints silently 404 (regression
    # observed after uni restart moni / supervisor-launched restarts).
    # 3.12 is the version moni's pinned deps were installed against.
    /usr/local/bin/python3 -c "
import uvicorn
from moni.web.server import create_app
uvicorn.run(create_app(), host='127.0.0.1', port=8900, log_level='error')
" &

    local pid=$!
    echo "$pid" > "$PIDFILE"

    # Verify the process is alive
    if kill -0 "$pid" 2>/dev/null; then
        echo "Started (PID $pid)"
    else
        rm -f "$PIDFILE"
        echo "Error: process exited immediately"
        exit 1
    fi
}

_stop() {
    if [ ! -s "$PIDFILE" ]; then
        echo "Stopped"
        return 0
    fi

    local pid
    pid=$(cat "$PIDFILE")

    if kill -0 "$pid" 2>/dev/null; then
        kill "$pid"
        # Wait briefly for graceful shutdown
        local i=0
        while kill -0 "$pid" 2>/dev/null && [ $i -lt 10 ]; do
            sleep 0.2
            i=$((i + 1))
        done
        # Force kill if still alive
        kill -0 "$pid" 2>/dev/null && kill -9 "$pid" 2>/dev/null || true
        echo "Stopped"
    else
        echo "Stopped"
    fi

    rm -f "$PIDFILE"
}

_status() {
    if [ ! -s "$PIDFILE" ]; then
        echo "not running"
        return 0
    fi

    local pid
    pid=$(cat "$PIDFILE")

    if kill -0 "$pid" 2>/dev/null; then
        echo "running (PID $pid)"
    else
        echo "not running"
        rm -f "$PIDFILE"
    fi
}

case "${1:-}" in
    start) _start ;;
    stop)  _stop  ;;
    status) _status ;;
    *)
        echo "Usage: $0 {start|stop|status}" >&2
        exit 1
        ;;
esac
