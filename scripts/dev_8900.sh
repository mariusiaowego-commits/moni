#!/usr/bin/env bash
# dev_8900.sh — start moni web (port 8900, v1.0.9.3) with .env loaded.
#
# v1.0.9.3 fix: dev_8900.py alone doesn't source .env — BESTBLOGS_API_KEY
# ends up empty in the worker subprocess, so BB enhancement silently
# skips.  This wrapper does `set -a && source .env && set +a` first,
# matching scripts/web.sh's pattern.
#
# Usage:
#   bash scripts/dev_8900.sh start
#   bash scripts/dev_8900.sh stop
#   bash scripts/dev_8900.sh status

set -e
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="$REPO_DIR/data/.web.8900.pid"
PORT=8900

case "${1:-start}" in
  start)
    if lsof -ti :"$PORT" >/dev/null 2>&1; then
      echo "8900 already running"
      lsof -ti :"$PORT"
      exit 0
    fi
    cd "$REPO_DIR"
    set -a
    # shellcheck disable=SC1091
    . "$REPO_DIR/.env"
    set +a
    /usr/local/bin/python3 scripts/dev_8900.py >"$REPO_DIR/data/artifacts/web.log" 2>&1 &
    echo $! > "$PID_FILE"
    sleep 2
    if lsof -ti :"$PORT" >/dev/null 2>&1; then
      echo "8900 started, pid=$(lsof -ti :"$PORT")"
    else
      echo "8900 FAILED to start; check $REPO_DIR/data/artifacts/web.log"
      exit 1
    fi
    ;;
  stop)
    for pid in $(lsof -ti :"$PORT" 2>/dev/null); do
      # v1.0.9.3: only kill the actual Python uvicorn process, not
      # any transient Chrome processes that may briefly share the
      # port during browser navigation.  ps comm may be the full
      # path on macOS, so compare against the basename.
      comm=$(ps -o comm= -p "$pid" 2>/dev/null | xargs -I{} basename {} || echo "")
      case "$comm" in
        Python|python3|python3.1|python)
          kill "$pid" && echo "8900 stopped (pid $pid)" || echo "kill $pid failed"
          ;;
        *)
          echo "skip non-Python pid $pid (comm=$comm) on port $PORT"
          ;;
      esac
    done
    rm -f "$PID_FILE"
    ;;
  restart)
    "$0" stop
    sleep 1
    "$0" start
    ;;
  status)
    pid=$(lsof -ti :"$PORT" 2>/dev/null || true)
    if [[ -n "$pid" ]]; then
      echo "8900 running, pid=$pid"
    else
      echo "8900 not running"
    fi
    ;;
  *)
    echo "usage: $0 {start|stop|status}"
    exit 1
    ;;
esac
