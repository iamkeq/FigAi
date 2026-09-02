#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/platform.sh"

if [[ "$MATTGPT_PLATFORM" == "darwin" ]]; then
  launchctl print "gui/$UID/$APP_ID" || {
    printf 'MattGPT is not loaded.\n' >&2
    exit 1
  }
  printf 'Recent stderr:\n'
  tail -n 20 "$LOG_DIR/stderr.log" 2>/dev/null || true
  printf 'Recent stdout:\n'
  tail -n 20 "$LOG_DIR/stdout.log" 2>/dev/null || true
else
  systemctl --user status "$SERVICE_NAME" --no-pager
  printf 'Recent logs:\n'
  journalctl --user-unit "$SERVICE_NAME" -n 40 --no-pager
fi
