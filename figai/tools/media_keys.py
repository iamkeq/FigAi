"""media_key: simulate a hardware media key press (play/pause, next, previous,
volume up/down/mute). Real implementation on Windows via ctypes; reports
unsupported elsewhere rather than faking it."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry
from figai.tools.platform_errors import UnsupportedPlatformError

# Virtual-key codes for Windows media keys.
VK_CODES = {
    "play_pause": 0xB3,
    "next_track": 0xB0,
    "previous_track": 0xB1,
    "volume_up": 0xAF,
    "volume_down": 0xAE,
    "mute": 0xAD,
}

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002


def press_media_key_windows(action: str) -> None:
    import ctypes

    vk_code = VK_CODES[action]
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY, 0)
    user32.keybd_event(vk_code, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)


def default_press_media_key(action: str) -> None:
    if action not in VK_CODES:
        raise ValueError(f"Unknown media key action: {action}")
    if sys.platform == "win32":
        press_media_key_windows(action)
        return
    raise UnsupportedPlatformError(f"media_key is not supported on {sys.platform}")


def register_media_key_tool(
    registry: ToolRegistry, press: Callable[[str], None] = default_press_media_key
) -> None:
    def _media_key(arguments: dict[str, Any]) -> str:
        action = arguments["action"]
        try:
            press(action)
        except (ValueError, UnsupportedPlatformError) as exc:
            return json.dumps({"error": str(exc)})
        return json.dumps({"status": "pressed", "action": action})

    registry.register(
        Tool(
            name="media_key",
            description="Simulate a media key press: play_pause, next_track, previous_track, volume_up, volume_down, or mute.",
            parameters={
                "type": "object",
                "properties": {"action": {"type": "string", "enum": list(VK_CODES)}},
                "required": ["action"],
            },
            handler=_media_key,
        )
    )
