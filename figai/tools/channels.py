"""list_channels: resolves Slack channel names to IDs, so other tools (like
set_reminder's optional 'channel' argument) can target a specific channel by
name instead of needing a raw Slack channel ID."""

from __future__ import annotations

import json
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry

ChannelLister = Callable[[], list[dict[str, str]]]  # [{"id": ..., "name": ...}, ...]


def resolve_channel_id_by_name(list_channels: ChannelLister, name: str) -> str | None:
    normalized = name.lstrip("#").lower()
    for channel in list_channels():
        if channel["name"].lower() == normalized:
            return channel["id"]
    return None


def register_list_channels_tool(registry: ToolRegistry, list_channels: ChannelLister | None) -> None:
    def _list(_arguments: dict[str, Any]) -> str:
        if list_channels is None:
            return json.dumps({"error": "Channel listing is not available outside Slack."})
        try:
            channels = list_channels()
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)})
        return json.dumps(channels)

    registry.register(
        Tool(
            name="list_channels",
            description=(
                "List Slack channels Fig can see, with their names and IDs. Use this to resolve a channel "
                "name (e.g. 'general') to the ID needed by set_reminder's optional 'channel' argument, or "
                "any other tool that targets a specific Slack channel."
            ),
            parameters={"type": "object", "properties": {}},
            handler=_list,
        )
    )
