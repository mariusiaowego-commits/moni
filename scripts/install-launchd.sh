#!/usr/bin/env bash
# Install/uninstall the moni scheduler as a launchd service.
# Usage:
#   bash scripts/install-launchd.sh install
#   bash scripts/install-launchd.sh uninstall
#   bash scripts/install-launchd.sh status

set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLIST_SRC="$SCRIPT_DIR/com.moni.cron.plist"
PLIST_DEST="$HOME/Library/LaunchAgents/com.moni.cron.plist"

_install() {
    mkdir -p "$HOME/.moni"
    cp "$PLIST_SRC" "$PLIST_DEST"
    launchctl load -w "$PLIST_DEST"
    echo "✅ moni scheduler launchd service installed"
    echo "   PID: $(launchctl list com.moni.cron | awk '{print $1}')"
}

_uninstall() {
    if [[ -f "$PLIST_DEST" ]]; then
        launchctl unload -w "$PLIST_DEST" 2>/dev/null || true
        rm "$PLIST_DEST"
        echo "✅ moni scheduler launchd service removed"
    else
        echo "Not installed."
    fi
}

_status() {
    if launchctl list com.moni.cron &>/dev/null; then
        echo "moni cron: RUNNING"
        launchctl list com.moni.cron
    else
        echo "moni cron: NOT RUNNING"
    fi
}

case "${1:-status}" in
    install) _install ;;
    uninstall) _uninstall ;;
    status|*) _status ;;
esac
