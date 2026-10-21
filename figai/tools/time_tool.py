"""A trivial, read-only tool used to prove the CLI<->agent-loop wiring end-to-end.
Real Phase 1 tools (screenshot, system info, programs, files, SSH) land next."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from figai.agent.tools import Tool, ToolRegistry


def _get_current_time(_arguments: dict[str, Any]) -> str:
    return datetime.now().astimezone().isoformat()


def register_time_tool(registry: ToolRegistry) -> None:
    registry.register(
        Tool(
            name="get_current_time",
            description="Get the current local date and time on the machine Fig runs on.",
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            handler=_get_current_time,
        )
    )
