#!/bin/bash
# Run RedZone Everything automatically at login (and restart it if it crashes).
#   scripts/install_service.sh          install + start
#   scripts/install_service.sh remove   stop + uninstall
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
PLIST="$HOME/Library/LaunchAgents/com.redzone.everything.plist"
if [[ "${1:-}" == "remove" ]]; then
  launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
  rm -f "$PLIST"
  echo "Removed. RedZone won't start at login anymore."
  exit 0
fi
mkdir -p "$ROOT/data" "$HOME/Library/LaunchAgents"
sed "s|__ROOT__|$ROOT|g" "$ROOT/deploy/com.redzone.everything.plist.template" > "$PLIST"
launchctl bootout "gui/$(id -u)" "$PLIST" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$PLIST"
echo "Installed. RedZone starts at login and restarts if it crashes."
echo "Logs: $ROOT/data/redzone.log"
