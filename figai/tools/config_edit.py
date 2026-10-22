"""get_config / propose_config_change / resolve_config_change: lets Fig edit his own
settings from chat -- config.yaml and a short allowlist of NON-secret .env keys
(SSH host aliases, Gmail address, service URLs). Secrets (API keys, tokens, passwords),
the allowlists that bound what Fig may touch, and limits are deliberately not editable
here; set those by hand. Every change is confirm-gated, validated before it's written,
backed up (<file>.bak), and takes effect after a restart."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from figai.agent.tools import Tool, ToolRegistry
from figai.config import DEFAULT_CONFIG_PATH, DEFAULT_ENV_PATH, AppConfig, SshHost
from figai.logging_setup import get_logger
from figai.tools.confirm import ConfirmationStore

logger = get_logger("tools.config_edit")
ACTION_TYPE = "config_change"

# Top-level config.yaml sections Fig may edit. Deliberately absent: allowlisted_folders,
# allowlisted_programs (they bound what Fig can touch), limits, state_path.
YAML_EDITABLE = {"models", "vault", "voice", "startup_announcement_channel", "browser_bridge_port", "log_level"}
ENV_EDITABLE_EXACT = {"GMAIL_ADDRESS", "SONARR_URL", "RADARR_URL", "SABNZBD_URL", "OBS_WS_HOST", "OBS_WS_PORT"}
ENV_EDITABLE_PREFIX = "SSH_HOST_"
RESTART_NOTE = "Takes effect after a restart (say '!restart')."


class ConfigEditError(Exception):
    pass


def _env_key_ok(key: str) -> bool:
    if not re.fullmatch(r"[A-Z][A-Z0-9_]*", key):
        return False
    return key in ENV_EDITABLE_EXACT or (key.startswith(ENV_EDITABLE_PREFIX) and len(key) > len(ENV_EDITABLE_PREFIX))


def _validate_env(key: str, value: str) -> None:
    if not value or "\n" in value or "\r" in value:
        raise ConfigEditError("The value must be a single non-empty line.")
    if key.startswith(ENV_EDITABLE_PREFIX):
        parts = dict(item.split("=", 1) for item in value.split(";") if "=" in item)
        try:
            SshHost(
                host=parts["host"], user=parts["user"], port=int(parts.get("port", 22)), key_path=parts["key_path"]
            )
        except (KeyError, ValueError, ValidationError) as exc:
            raise ConfigEditError(
                "SSH host values look like 'host=192.168.1.5;user=name;key_path=C:\\path\\to\\key;port=22' "
                f"(port optional): {exc}"
            ) from exc
    elif key.endswith("_URL") and not value.startswith(("http://", "https://")):
        raise ConfigEditError(f"{key} must be an http(s) URL.")
    elif key == "OBS_WS_PORT" and not value.isdigit():
        raise ConfigEditError("OBS_WS_PORT must be a number.")


def _quote_env(value: str) -> str:
    return f'"{value}"' if re.search(r'[\s#"\']', value) else value


def _set_env_line(text: str, key: str, value: str) -> str:
    line = f"{key}={_quote_env(value)}"
    lines = text.splitlines()
    for i, existing in enumerate(lines):
        if re.match(rf"\s*{re.escape(key)}\s*=", existing):
            lines[i] = line
            break
    else:
        lines.append(line)
    return "\n".join(lines) + "\n"


def _set_dotted(raw: dict[str, Any], dotted: str, value: Any) -> dict[str, Any]:
    parts = dotted.split(".")
    node = raw
    for part in parts[:-1]:
        child = node.setdefault(part, {})
        if not isinstance(child, dict):
            raise ConfigEditError(f"'{part}' in config.yaml isn't a section, so '{dotted}' can't be set.")
        node = child
    node[parts[-1]] = value
    return raw


def _replace_scalar_in_text(text: str, dotted: str, value: Any) -> str | None:
    """Edits an existing scalar line in place so comments in config.yaml survive.
    Returns None when the key isn't a plain existing scalar line (caller falls back)."""
    if isinstance(value, (dict, list)):
        return None
    parts = dotted.split(".")
    lines = text.splitlines()
    stack: list[tuple[int, str]] = []
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(line) - len(line.lstrip())
        match = re.match(r"([A-Za-z0-9_]+):(.*)$", stripped)
        if not match:
            continue
        while stack and stack[-1][0] >= indent:
            stack.pop()
        path = [name for _, name in stack] + [match.group(1)]
        if path == parts and match.group(2).strip():
            comment = re.search(r"\s+#.*$", match.group(2))
            lines[i] = f"{' ' * indent}{parts[-1]}: {yaml.safe_dump(value, default_flow_style=True).strip().removesuffix('...').strip()}"
            if comment:
                lines[i] += comment.group(0)
            return "\n".join(lines) + "\n"
        if not match.group(2).strip():
            stack.append((indent, match.group(1)))
    return None


def _parse_value(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except ValueError:
            return value
    return value


def register_config_edit_tools(
    registry: ToolRegistry,
    confirmations: ConfirmationStore,
    config_path: Path = DEFAULT_CONFIG_PATH,
    env_path: Path = DEFAULT_ENV_PATH,
) -> None:
    def _get(_arguments: dict[str, Any]) -> str:
        config_text = config_path.read_text(encoding="utf-8") if config_path.exists() else ""
        env_values: dict[str, str] = {}
        other_keys: list[str] = []
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                match = re.match(r"\s*([A-Z][A-Z0-9_]*)\s*=(.*)$", line)
                if not match:
                    continue
                key = match.group(1)
                if _env_key_ok(key):
                    env_values[key] = match.group(2).strip().strip('"')
                else:
                    other_keys.append(key)
        return json.dumps(
            {
                "config_yaml": config_text,
                "env_editable": env_values,
                "env_secret_keys_set_but_hidden": other_keys,
                "editable_config_sections": sorted(YAML_EDITABLE),
                "editable_env_keys": sorted(ENV_EDITABLE_EXACT) + [f"{ENV_EDITABLE_PREFIX}<alias>"],
            }
        )

    registry.register(
        Tool(
            name="get_config",
            description=(
                "Show Fig's config.yaml and the non-secret .env settings (SSH host aliases, Gmail address, "
                "service URLs). Secret values (API keys, tokens, passwords) are never shown."
            ),
            parameters={"type": "object", "properties": {}},
            handler=_get,
        )
    )

    def _propose(arguments: dict[str, Any]) -> str:
        target = arguments.get("file")
        key = str(arguments.get("key", "")).strip()
        try:
            if target == "config":
                value = _parse_value(arguments["value"])
                if key.split(".")[0] not in YAML_EDITABLE:
                    raise ConfigEditError(
                        f"'{key}' can't be changed from chat. Editable sections: {', '.join(sorted(YAML_EDITABLE))}. "
                        "Allowlists, limits and secrets must be edited by hand."
                    )
                raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
                try:
                    AppConfig(**_set_dotted(raw, key, value))
                except ValidationError as exc:
                    raise ConfigEditError(f"That would make config.yaml invalid, so I won't: {exc}") from exc
                preview = f"config.yaml: set {key} = {json.dumps(value)}"
                payload = {"file": "config", "key": key, "value": value}
            elif target == "env":
                value = str(arguments["value"]).strip()
                if not _env_key_ok(key):
                    raise ConfigEditError(
                        f"'{key}' can't be changed from chat. Editable .env keys: "
                        f"{', '.join(sorted(ENV_EDITABLE_EXACT))}, {ENV_EDITABLE_PREFIX}<alias>. "
                        "API keys, tokens and passwords must be edited by hand."
                    )
                _validate_env(key, value)
                preview = f".env: set {key} = {value}"
                payload = {"file": "env", "key": key, "value": value}
            else:
                raise ConfigEditError("file must be 'config' (config.yaml) or 'env' (.env).")
        except ConfigEditError as exc:
            return json.dumps({"error": str(exc)})
        except (OSError, yaml.YAMLError, KeyError) as exc:
            return json.dumps({"error": f"Couldn't prepare that change: {exc}"})
        token = confirmations.propose(ACTION_TYPE, payload, preview)
        logger.info("Proposed config change token=%s %s", token, preview)
        return json.dumps(
            {
                "status": "awaiting_confirmation",
                "token": token,
                "preview": preview,
                "note": "Wait for the user's explicit yes in a LATER message before calling resolve_config_change.",
            }
        )

    registry.register(
        Tool(
            name="propose_config_change",
            description=(
                "Draft changing ONE setting. file='config' edits config.yaml with a dotted key (e.g. "
                "'vault.ssh_host_alias', 'startup_announcement_channel'; value may be JSON for numbers/booleans). "
                "file='env' edits a non-secret .env key (SSH_HOST_<alias>, GMAIL_ADDRESS, SONARR_URL, RADARR_URL, "
                "SABNZBD_URL, OBS_WS_HOST, OBS_WS_PORT). API keys, tokens, passwords, allowlists and limits can't "
                "be changed this way. Nothing changes until the user confirms in a later message."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "file": {"type": "string", "enum": ["config", "env"]},
                    "key": {"type": "string"},
                    "value": {"type": "string"},
                },
                "required": ["file", "key", "value"],
            },
            handler=_propose,
        )
    )

    def _resolve(arguments: dict[str, Any]) -> str:
        try:
            payload = confirmations.resolve(arguments["token"], ACTION_TYPE)
        except (KeyError, TimeoutError) as exc:
            return json.dumps({"error": str(exc)})
        try:
            if payload["file"] == "config":
                original = config_path.read_text(encoding="utf-8")
                new_text = _replace_scalar_in_text(original, payload["key"], payload["value"])
                if new_text is None:
                    raw = yaml.safe_load(original) or {}
                    new_text = yaml.safe_dump(_set_dotted(raw, payload["key"], payload["value"]), sort_keys=False)
                    note = " (config.yaml was rewritten, so its comments were dropped; the original is in config.yaml.bak)"
                else:
                    note = ""
                AppConfig(**(yaml.safe_load(new_text) or {}))  # final check on what will actually be written
                target = config_path
            else:
                original = env_path.read_text(encoding="utf-8") if env_path.exists() else ""
                new_text = _set_env_line(original, payload["key"], payload["value"])
                note = ""
                target = env_path
            target.with_name(target.name + ".bak").write_text(original, encoding="utf-8")
            target.write_text(new_text, encoding="utf-8")
        except (OSError, ValidationError, yaml.YAMLError, ConfigEditError) as exc:
            return json.dumps({"error": f"Nothing was changed: {exc}"})
        logger.info("Applied config change %s", payload["key"])
        return json.dumps({"status": "saved", "change": f"{payload['file']}:{payload['key']}", "note": RESTART_NOTE + note})

    registry.register(
        Tool(
            name="resolve_config_change",
            description="Apply a config change the user confirmed (token from propose_config_change). Backs up the file first.",
            parameters={"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
            handler=_resolve,
        )
    )
