"""add_check / list_checks / run_check / remove_check: scheduled up/down monitoring."""

from __future__ import annotations

import json
from typing import Any

from figai.agent.tools import Tool, ToolRegistry
from figai.monitoring.checks import CheckError, CheckManager
from figai.reminders.context import ConversationContext


def register_check_tools(
    registry: ToolRegistry, manager: CheckManager, context: ConversationContext, owner_channel: str
) -> None:
    def _add(arguments: dict[str, Any]) -> str:
        channel = arguments.get("channel") or context.current_channel or owner_channel
        try:
            check = manager.add(
                arguments["name"],
                arguments["target"],
                float(arguments.get("every_minutes", 15)),
                channel,
                arguments.get("kind"),
            )
        except CheckError as exc:
            return json.dumps({"error": str(exc)})
        manager.run_now(check.name)  # first result straight away; alerts only start after repeated failures
        return json.dumps({"status": "added", "check": manager.describe(check), "alerts_go_to": channel})

    registry.register(
        Tool(
            name="add_check",
            description=(
                "Start monitoring a device or site: Fig checks it on a schedule and tells the user ONLY when "
                "it goes down (after 2 failed checks in a row) and when it comes back up. target is a URL "
                "(http check, up if it answers below 400) or host:port (tcp check). Alerts go to the "
                "current conversation unless 'channel' (a channel ID from list_channels) is given."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Short unique name, e.g. 'FigAxe2'."},
                    "target": {"type": "string", "description": "e.g. http://192.168.50.57/ or 192.168.50.57:80"},
                    "every_minutes": {"type": "number", "description": "Check interval, default 15, minimum 1."},
                    "kind": {"type": "string", "enum": ["http", "tcp"]},
                    "channel": {"type": "string"},
                },
                "required": ["name", "target"],
            },
            handler=_add,
        )
    )

    registry.register(
        Tool(
            name="list_checks",
            description="List the scheduled up/down checks and whether each is currently up.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _a: json.dumps({"checks": [manager.describe(c) for c in manager.list()] or ["(none)"]}),
        )
    )

    def _run(arguments: dict[str, Any]) -> str:
        try:
            return json.dumps({"result": manager.describe(manager.run_now(arguments["name"]))})
        except CheckError as exc:
            return json.dumps({"error": str(exc)})

    registry.register(
        Tool(
            name="run_check",
            description="Run one scheduled check right now and report the result.",
            parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            handler=_run,
        )
    )

    def _remove(arguments: dict[str, Any]) -> str:
        if manager.remove(arguments["name"]):
            return json.dumps({"status": "removed", "name": arguments["name"]})
        return json.dumps({"error": f"No check named '{arguments['name']}'."})

    registry.register(
        Tool(
            name="remove_check",
            description="Stop monitoring something (delete a scheduled check).",
            parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            handler=_remove,
        )
    )
