#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd -- "$SCRIPT_DIR/.." && pwd)"
source "$SCRIPT_DIR/platform.sh"

require_env_file

mkdir -p "$BIN_DIR" "$LOG_DIR" "$(dirname "$SERVICE_PATH")"
chmod 700 "$DATA_DIR" "$CONFIG_DIR" "$BIN_DIR" "$LOG_DIR" "$(dirname "$SERVICE_PATH")"
cd "$PROJECT_DIR"
bun install --frozen-lockfile
bun run check
bun test
bun run build
install -m 755 "$PROJECT_DIR/dist/figai" "$EXECUTABLE"

if [[ "$FIGAI_PLATFORM" == "darwin" ]]; then
  sed \
    -e "s|__EXECUTABLE__|$EXECUTABLE|g" \
    -e "s|__WORKING_DIRECTORY__|$PROJECT_DIR|g" \
    -e "s|__LOG_DIRECTORY__|$LOG_DIR|g" \
    "$PROJECT_DIR/launchd/com.matgra.figai.plist.template" > "$SERVICE_PATH"
  plutil -lint "$SERVICE_PATH"

  launchctl bootout "gui/$UID/$APP_ID" 2>/dev/null || true
  bootstrapped=0
  for _attempt in 1 2 3; do
    if launchctl bootstrap "gui/$UID" "$SERVICE_PATH"; then
      bootstrapped=1
      break
    fi
    sleep 1
  done
  if [[ "$bootstrapped" != "1" ]]; then
    printf 'FigAi could not be loaded after three attempts.\n' >&2
    exit 1
  fi
  launchctl enable "gui/$UID/$APP_ID"
  launchctl kickstart -k "gui/$UID/$APP_ID"
else
  if ! command -v systemctl >/dev/null 2>&1; then
    printf 'systemctl is required to install FigAi as a Linux user service.\n' >&2
    exit 1
  fi
  sed \
    -e "s|__EXECUTABLE__|$EXECUTABLE|g" \
    -e "s|__WORKING_DIRECTORY__|$PROJECT_DIR|g" \
    -e "s|__ENV_PATH__|$ENV_PATH|g" \
    "$PROJECT_DIR/systemd/figai.service.template" > "$SERVICE_PATH"
  chmod 600 "$SERVICE_PATH"
  systemctl --user daemon-reload
  systemctl --user enable --now "$SERVICE_NAME"
  systemctl --user restart "$SERVICE_NAME"
fi

printf 'FigAi installed and started on %s.\n' "$FIGAI_PLATFORM"
