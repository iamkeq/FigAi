from __future__ import annotations

import json

from figai.agent.tools import ToolRegistry
from figai.tools.channels import register_list_channels_tool


def test_list_channels_returns_lister_output() -> None:
    registry = ToolRegistry()
    register_list_channels_tool(registry, lambda: [{"id": "C1", "name": "general"}])

    result = json.loads(registry.execute("list_channels", "{}"))

    assert result == [{"id": "C1", "name": "general"}]


def test_list_channels_unavailable_outside_slack_returns_error() -> None:
    registry = ToolRegistry()
    register_list_channels_tool(registry, None)

    result = json.loads(registry.execute("list_channels", "{}"))

    assert "error" in result


def test_list_channels_handles_lister_failure_without_raising() -> None:
    def failing_lister():
        raise RuntimeError("Slack API error")

    registry = ToolRegistry()
    register_list_channels_tool(registry, failing_lister)

    result = json.loads(registry.execute("list_channels", "{}"))

    assert "error" in result
