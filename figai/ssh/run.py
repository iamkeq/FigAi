"""ssh_run: a two-step propose/confirm flow. Drafting a command never executes it --
only an explicit, later confirmation that resolves the returned token does."""

from __future__ import annotations

import json
from typing import Any

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config
from figai.logging_setup import get_logger
from figai.ssh.client import run_command
from figai.tools.confirm import ConfirmationStore

logger = get_logger("ssh.run")

ACTION_TYPE = "ssh_run"
COMMAND_TIMEOUT_SECONDS = 30


def register_ssh_run_tools(registry: ToolRegistry, config: Config, confirmations: ConfirmationStore) -> None:
    def _propose(arguments: dict[str, Any]) -> str:
        alias = arguments["host_alias"].lower()
        command = arguments["command"]
        if alias not in config.settings.ssh_hosts:
            configured = sorted(config.settings.ssh_hosts)
            return json.dumps({"error": f"Unknown host alias '{alias}'. Configured hosts: {configured}"})

        preview = f"Run on '{alias}': {command}"
        token = confirmations.propose(ACTION_TYPE, {"host_alias": alias, "command": command}, preview)
        logger.info("Proposed ssh_run token=%s alias=%s", token, alias)
        return json.dumps(
            {
                "status": "awaiting_confirmation",
                "token": token,
                "preview": preview,
                "note": (
                    "Tell the user exactly what will run and wait for their explicit yes in a "
                    "LATER message before calling resolve_ssh_command with this token."
                ),
            }
        )

    registry.register(
        Tool(
            name="propose_ssh_command",
            description=(
                "Draft a command to run on a configured remote host. Does not execute anything -- "
                "the user must explicitly confirm in a later message before resolve_ssh_command runs it."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "host_alias": {"type": "string"},
                    "command": {"type": "string"},
                },
                "required": ["host_alias", "command"],
            },
            handler=_propose,
        )
    )

    def _resolve(arguments: dict[str, Any]) -> str:
        token = arguments["token"]
        try:
            payload = confirmations.resolve(token, ACTION_TYPE)
        except (KeyError, TimeoutError) as exc:
            return json.dumps({"error": str(exc)})

        host = config.settings.ssh_hosts[payload["host_alias"]]
        result = run_command(host, payload["command"], timeout=COMMAND_TIMEOUT_SECONDS)
        logger.info("ssh_run executed alias=%s exit_code=%s", payload["host_alias"], result.exit_code)
        return json.dumps(
            {
                "exit_code": result.exit_code,
                "timed_out": result.timed_out,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
        )

    registry.register(
        Tool(
            name="resolve_ssh_command",
            description=(
                "Execute a previously proposed command, after the user has explicitly confirmed it "
                "in a later message. Never call this in the same turn the command was proposed."
            ),
            parameters={"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
            handler=_resolve,
        )
    )
