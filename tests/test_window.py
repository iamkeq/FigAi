from __future__ import annotations

import json

from figai.agent.tools import ToolRegistry
from figai.tools.platform_errors import UnsupportedPlatformError
from figai.tools.window import register_active_window_tool


def test_active_window_returns_title_from_injected_getter() -> None:
    registry = ToolRegistry()
    register_active_window_tool(registry, get_title=lambda: "Notepad")

    result = json.loads(registry.execute("active_window", "{}"))

    assert result == {"title": "Notepad"}


def test_active_window_reports_unsupported_platform_cleanly() -> None:
    def unsupported() -> str:
        raise UnsupportedPlatformError("active_window is not supported on linux")

    registry = ToolRegistry()
    register_active_window_tool(registry, get_title=unsupported)

    result = json.loads(registry.execute("active_window", "{}"))

    assert "error" in result
