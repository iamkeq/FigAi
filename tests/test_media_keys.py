from __future__ import annotations

import json

from figai.agent.tools import ToolRegistry
from figai.tools.media_keys import register_media_key_tool


def test_media_key_calls_press_with_action() -> None:
    pressed: list[str] = []
    registry = ToolRegistry()
    register_media_key_tool(registry, press=pressed.append)

    result = json.loads(registry.execute("media_key", json.dumps({"action": "play_pause"})))

    assert result == {"status": "pressed", "action": "play_pause"}
    assert pressed == ["play_pause"]


def test_media_key_reports_unsupported_platform_cleanly() -> None:
    from figai.tools.platform_errors import UnsupportedPlatformError

    def unsupported(action: str) -> None:
        raise UnsupportedPlatformError("media_key is not supported on linux")

    registry = ToolRegistry()
    register_media_key_tool(registry, press=unsupported)

    result = json.loads(registry.execute("media_key", json.dumps({"action": "mute"})))

    assert "error" in result


def test_media_key_invalid_action_returns_error_not_raise() -> None:
    def press(action: str) -> None:
        raise ValueError(f"Unknown media key action: {action}")

    registry = ToolRegistry()
    register_media_key_tool(registry, press=press)

    result = json.loads(registry.execute("media_key", json.dumps({"action": "not_a_real_action"})))

    assert "error" in result
