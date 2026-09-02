#!/usr/bin/env bash

set -euo pipefail

APP_ID="com.matgra.mattgpt"
SERVICE_NAME="mattgpt.service"
PLATFORM_NAME="$(uname -s)"

case "$PLATFORM_NAME" in
  Darwin)
    MATTGPT_PLATFORM="darwin"
    DATA_DIR="$HOME/Library/Application Support/MattGPT"
    CONFIG_DIR="$DATA_DIR"
    LOG_DIR="$HOME/Library/Logs/MattGPT"
    SERVICE_PATH="$HOME/Library/LaunchAgents/$APP_ID.plist"
    ;;
  Linux)
    MATTGPT_PLATFORM="linux"
    DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/mattgpt"
    CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/mattgpt"
    STATE_DIR="${XDG_STATE_HOME:-$HOME/.local/state}/mattgpt"
    LOG_DIR="$STATE_DIR"
    SERVICE_PATH="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/$SERVICE_NAME"
    ;;
  *)
    printf 'MattGPT supports macOS and Linux; found %s.\n' "$PLATFORM_NAME" >&2
    exit 1
    ;;
esac

BIN_DIR="$DATA_DIR/bin"
ENV_PATH="$CONFIG_DIR/.env"
EXECUTABLE="$BIN_DIR/mattgpt"

file_mode() {
  if [[ "$MATTGPT_PLATFORM" == "darwin" ]]; then
    stat -f '%Lp' "$1"
  else
    stat -c '%a' "$1"
  fi
}

require_env_file() {
  if [[ ! -f "$ENV_PATH" ]]; then
    printf 'Missing %s. Copy .env.example there and fill it in first.\n' "$ENV_PATH" >&2
    exit 1
  fi
  local mode
  mode="$(file_mode "$ENV_PATH")"
  if [[ "$mode" != "600" ]]; then
    printf '%s must have mode 0600 (currently %s).\n' "$ENV_PATH" "$mode" >&2
    exit 1
  fi
}
