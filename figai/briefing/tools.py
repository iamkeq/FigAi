"""briefing_status / set_briefing / send_briefing_now: control the daily briefing from chat."""

from __future__ import annotations

import json
from typing import Any

from figai.agent.tools import Tool, ToolRegistry
from figai.briefing.briefing import BriefingManager


def register_briefing_tools(registry: ToolRegistry, manager: BriefingManager) -> None:
    registry.register(
        Tool(
            name="briefing_status",
            description="Show whether the daily briefing is on, when it's sent, and where.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _a: json.dumps({"status": manager.status()}),
        )
    )

    def _set(arguments: dict[str, Any]) -> str:
        try:
            manager.configure(
                enabled=arguments.get("enabled"),
                time=arguments.get("time"),
                include_email=arguments.get("include_email"),
                channel=arguments.get("channel"),
            )
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"status": manager.status()})

    registry.register(
        Tool(
            name="set_briefing",
            description=(
                "Turn the daily morning briefing on or off, change its time (24h HH:MM, local), whether it "
                "includes the email summary (the only part that costs model tokens), or the Slack channel ID "
                "it goes to (resolve names with list_channels; empty string = your DM). Changes persist."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "enabled": {"type": "boolean"},
                    "time": {"type": "string"},
                    "include_email": {"type": "boolean"},
                    "channel": {"type": "string"},
                },
            },
            handler=_set,
        )
    )

    def _now(_arguments: dict[str, Any]) -> str:
        try:
            manager.send_now()
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Couldn't build the briefing: {exc}"})
        return json.dumps({"status": "sent"})

    registry.register(
        Tool(
            name="send_briefing_now",
            description="Send the briefing right now (works even if the daily schedule is off).",
            parameters={"type": "object", "properties": {}},
            handler=_now,
        )
    )
