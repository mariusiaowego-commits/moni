#!/usr/bin/env bash
# scripts/web-8901.sh — dev/test uvicorn process manager (port 8901)
#
# v1.0.5.10: dev/test port for the UI overhaul.  Use this when
# hacking on moni/web/static/* or anything else you want to verify
# visually before sending a PR.  Port 8900 (web.sh) is the
# "production" port — it stays on whatever's currently on `main`,
# and only picks up new code after PR merge + restart.
#
# Commands:
#   start  — spawn uvicorn on 127.0.0.1:8901 in background
#   stop   — kill the PID, remove pidfile
#   status — check if the PID is alive
#
# PID file: data/.web-8901.pid (distinct from .web.pid so the two
# scripts don't trip over each other).

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
PIDFILE="$REPO_DIR/data/.web-8901.pid"
PORT=8901

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

    # v1.0.5.10: dev port shares the prod DB by default.  Set
    # MONI_DATA_DIR=/path/to/wt/data when running inside a worktree
    # if you want DB isolation.
    python3 -c "
import uvicorn
from moni.web.server import create_app
uvicorn.run(create_app(), host='127.0.0.1', port=$PORT, log_level='warning')
" &

    local pid=$!
    echo "$pid" > "$PIDFILE"

    # Verify the process is alive
    if kill -0 "$pid" 2>/dev/null; then
        echo "Started (PID $pid) on http://127.0.0.1:$PORT/"
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
        echo "running (PID $pid) on port $PORT"
    else
        echo "not running"
        rm -f "$PIDFILE"
    fi
}

case "${1:-}" in
    start)  _start  ;;
    stop)   _stop   ;;
    status) _status ;;
    *)
        echo "Usage: $0 {start|stop|status}  (dev port $PORT)" >&2
        exit 1
        ;;
esac