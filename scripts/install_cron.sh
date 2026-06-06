#!/usr/bin/env bash
# scripts/install_cron.sh — register/unregister default_worker.sh in crontab
#
# Registers a 5-minute crontab entry that runs scripts/default_worker.sh,
# which scans ~/.moni/queue/*.json and processes any pending share manifests
# via the default profile Hermes agent.
#
# Usage:
#   ./scripts/install_cron.sh install    # add the 5-minute crontab entry
#   ./scripts/install_cron.sh uninstall  # remove the entry
#   ./scripts/install_cron.sh status     # show whether the entry is installed
#   ./scripts/install_cron.sh show       # print the current user crontab
#
# The crontab marker "# moni-default-worker" is used to find/remove our entry
# without touching any other cron jobs the user may have.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_DIR="$(dirname "$SCRIPT_DIR")"
WORKER="$REPO_DIR/scripts/default_worker.sh"

CRON_MARKER="# moni-default-worker"
CRON_LINE="*/5 * * * * /bin/bash $WORKER >> $HOME/.moni/cron.log 2>&1 $CRON_MARKER"

_install() {
    if [[ ! -x "$WORKER" ]]; then
        echo "Error: $WORKER is not executable" >&2
        exit 1
    fi

    # Check if marker is already present
    if crontab -l 2>/dev/null | grep -qF "$CRON_MARKER"; then
        echo "Already installed (marker $CRON_MARKER found in crontab)"
        echo "Run '$0 uninstall' first to remove"
        exit 0
    fi

    # Append our line to the existing crontab
    (crontab -l 2>/dev/null || true; echo "$CRON_LINE") | crontab -
    echo "Installed:"
    echo "  $CRON_LINE"
    echo ""
    echo "Verify with: crontab -l | grep moni-default-worker"
    echo "Logs: tail -f $HOME/.moni/cron.log"
}

_uninstall() {
    if ! crontab -l 2>/dev/null | grep -qF "$CRON_MARKER"; then
        echo "Not installed (no marker $CRON_MARKER in crontab)"
        exit 0
    fi

    # Remove the line containing our marker (preserve all other cron entries)
    crontab -l 2>/dev/null | grep -vF "$CRON_MARKER" | crontab -
    echo "Uninstalled (removed line with marker $CRON_MARKER)"
}

_status() {
    if crontab -l 2>/dev/null | grep -qF "$CRON_MARKER"; then
        echo "installed"
        crontab -l 2>/dev/null | grep -F "$CRON_MARKER"
    else
        echo "not installed"
    fi
}

_show() {
    crontab -l 2>/dev/null || echo "(no crontab)"
}

case "${1:-}" in
    install)   _install ;;
    uninstall) _uninstall ;;
    status)    _status ;;
    show)      _show ;;
    *)
        echo "Usage: $0 {install|uninstall|status|show}" >&2
        exit 1
        ;;
esac
