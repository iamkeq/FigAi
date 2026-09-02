#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/platform.sh"
timestamp="$(date +%s)"

if [[ "$FIGAI_PLATFORM" == "darwin" ]]; then
  launchctl bootout "gui/$UID/$APP_ID" 2>/dev/null || true
  if [[ -f "$SERVICE_PATH" ]]; then
    mv "$SERVICE_PATH" "$HOME/.Trash/$APP_ID.plist.$timestamp"
  fi
  if [[ -f "$EXECUTABLE" ]]; then mv "$EXECUTABLE" "$HOME/.Trash/figai.$timestamp"; fi
  printf 'FigAi service and executable moved to Trash. Data, configuration, backups, and logs were preserved.\n'
else
  systemctl --user disable --now "$SERVICE_NAME" 2>/dev/null || true
  recovery="$DATA_DIR/uninstalled/$timestamp"
  mkdir -p "$recovery"
  chmod 700 "$DATA_DIR/uninstalled" "$recovery"
  if [[ -f "$SERVICE_PATH" ]]; then mv "$SERVICE_PATH" "$recovery/$SERVICE_NAME"; fi
  if [[ -f "$EXECUTABLE" ]]; then mv "$EXECUTABLE" "$recovery/figai"; fi
  systemctl --user daemon-reload
  printf 'FigAi service and executable moved to %s. Data, configuration, backups, and logs were preserved.\n' "$recovery"
fi
