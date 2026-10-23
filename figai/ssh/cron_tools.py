"""Cron jobs on a remote host: list them, add/remove/pause/resume the ones Fig owns, and read the
cron log. Changing the crontab is persistent command execution, so every change is a
propose -> confirm -> apply flow (never auto-confirmed, even by voice)."""

from __future__ import annotations

import json
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config, SshHost
from figai.logging_setup import get_logger
from figai.ssh import cron
from figai.ssh.client import CommandResult, run_command, run_command_with_input
from figai.tools.confirm import ConfirmationStore

logger = get_logger("ssh.cron")

ACTION_TYPE = "cron_change"
ACTIONS = ("add", "remove", "enable", "disable")
FETCH_COMMAND = "crontab -l 2>/dev/null || true"
INSTALL_COMMAND = "crontab -l > ~/.fig-crontab.bak 2>/dev/null; crontab -"
LOG_COMMAND = (
    "(journalctl -u cron -u crond -u cronie -n 40 --no-pager 2>/dev/null || "
    "grep -i cron /var/log/syslog 2>/dev/null || grep -i cron /var/log/cron 2>/dev/null) | tail -n 40"
)

Runner = Callable[[SshHost, str, "str | None", int], CommandResult]


def default_runner(host: SshHost, command: str, stdin_text: str | None, timeout: int) -> CommandResult:
    if stdin_text is None:
        return run_command(host, command, timeout=timeout)
    return run_command_with_input(host, command, stdin_text, timeout=timeout)


def apply_change(crontab: str, action: str, name: str, schedule: str | None, command: str | None) -> str:
    if action == "add":
        if not schedule or not command:
            raise cron.CronError("Adding a job needs both a schedule and a command.")
        return cron.add_job(crontab, name, schedule, command)
    if action == "remove":
        return cron.remove_job(crontab, name)
    if action in ("enable", "disable"):
        return cron.set_enabled(crontab, name, action == "enable")
    raise cron.CronError(f"action must be one of {', '.join(ACTIONS)}")


def register_cron_tools(
    registry: ToolRegistry, config: Config, confirmations: ConfirmationStore, runner: Runner = default_runner
) -> None:
    def _host(arguments: dict[str, Any]) -> tuple[str, SshHost]:
        alias = (arguments.get("host") or config.app.cron_host_alias or config.app.vault.ssh_host_alias).lower()
        host = config.settings.ssh_hosts.get(alias)
        if host is None:
            raise cron.CronError(f"Unknown host '{alias}'. Configured hosts: {sorted(config.settings.ssh_hosts)}")
        return alias, host

    def _fetch(host: SshHost) -> str:
        result = runner(host, FETCH_COMMAND, None, 30)
        if result.exit_code != 0 or result.timed_out:
            raise cron.CronError(f"Couldn't read the crontab: {(result.stderr or 'timed out').strip()[:200]}")
        return result.stdout

    def _list(arguments: dict[str, Any]) -> str:
        try:
            alias, host = _host(arguments)
            entries = cron.parse(_fetch(host))
        except (cron.CronError, ConnectionError, OSError) as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps(
            {
                "host": alias,
                "jobs": [
                    {"name": e.name, "managed_by_fig": e.managed, "schedule": e.schedule, "command": e.command, "enabled": e.enabled}
                    for e in entries
                ]
                or [],
            }
        )

    registry.register(
        Tool(
            name="list_cron_jobs",
            description=(
                "List the cron jobs on a remote host (default: the home server). Jobs Fig created have a name "
                "and managed_by_fig=true; Fig can only change those."
            ),
            parameters={"type": "object", "properties": {"host": {"type": "string", "description": "SSH host alias."}}},
            handler=_list,
        )
    )

    def _propose(arguments: dict[str, Any]) -> str:
        action = arguments["action"]
        name = arguments["name"].strip()
        try:
            alias, host = _host(arguments)
            apply_change(_fetch(host), action, name, arguments.get("schedule"), arguments.get("command"))  # dry run
        except (cron.CronError, ConnectionError, OSError) as exc:
            return json.dumps({"error": str(exc)})
        if action == "add":
            preview = f"On '{alias}': add cron job '{name}': {' '.join(arguments['schedule'].split())} {arguments['command']}"
        else:
            preview = f"On '{alias}': {action} cron job '{name}'"
        token = confirmations.propose(
            ACTION_TYPE,
            {"alias": alias, "action": action, "name": name, "schedule": arguments.get("schedule"), "command": arguments.get("command")},
            preview,
        )
        return json.dumps(
            {
                "status": "awaiting_confirmation",
                "token": token,
                "preview": preview,
                "note": "Tell the user exactly what will change and wait for their explicit yes in a LATER message before calling resolve_cron_change.",
            }
        )

    registry.register(
        Tool(
            name="propose_cron_change",
            description=(
                "Draft a change to the remote crontab: action 'add' (needs name, schedule like '0 4 * * *', command), "
                "'remove', 'enable' or 'disable' (name only). Only jobs Fig created can be changed. Does nothing until "
                "the user confirms in a later message, then call resolve_cron_change."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "action": {"type": "string", "enum": list(ACTIONS)},
                    "name": {"type": "string", "description": "Short id, letters/digits/dashes."},
                    "schedule": {"type": "string"},
                    "command": {"type": "string"},
                    "host": {"type": "string"},
                },
                "required": ["action", "name"],
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
            alias, host = _host({"host": payload["alias"]})
            updated = apply_change(_fetch(host), payload["action"], payload["name"], payload["schedule"], payload["command"])
            result = runner(host, INSTALL_COMMAND, updated, 30)
            if result.exit_code != 0:
                return json.dumps({"error": f"crontab refused the change: {result.stderr.strip()[:300]}"})
            verified = {e.name: e for e in cron.parse(_fetch(host)) if e.name}
        except (cron.CronError, ConnectionError, OSError) as exc:
            return json.dumps({"error": f"Nothing was changed: {exc}"})
        name = payload["name"]
        present = any(n.lower() == name.lower() for n in verified)
        expected = payload["action"] != "remove"
        if present != expected:
            return json.dumps({"error": "The crontab was written but I couldn't confirm the change; check list_cron_jobs."})
        logger.info("Cron change applied on %s: %s %s", alias, payload["action"], name)
        return json.dumps({"status": "done", "host": alias, "action": payload["action"], "name": name, "backup": "~/.fig-crontab.bak"})

    registry.register(
        Tool(
            name="resolve_cron_change",
            description="Apply the cron change the user confirmed in a later message (token from propose_cron_change). Backs up the old crontab first.",
            parameters={"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
            handler=_resolve,
        )
    )

    def _log(arguments: dict[str, Any]) -> str:
        try:
            alias, host = _host(arguments)
            result = runner(host, LOG_COMMAND, None, 30)
        except (cron.CronError, ConnectionError, OSError) as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"host": alias, "log": result.stdout.strip()[-3000:] or "(no cron log lines found)"})

    registry.register(
        Tool(
            name="cron_log",
            description="Show the most recent cron log lines on a remote host (did my job actually run?).",
            parameters={"type": "object", "properties": {"host": {"type": "string"}}},
            handler=_log,
        )
    )
