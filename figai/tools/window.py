"""active_window: the title of the currently focused window. Real implementation
on Windows (via ctypes/user32); reports unsupported elsewhere rather than
faking a value, since Fig's deployment target is Windows."""

from __future__ import annotations

import json
import sys
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry
from figai.tools.platform_errors import UnsupportedPlatformError


def get_active_window_title_windows() -> str:
    import ctypes

    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    hwnd = user32.GetForegroundWindow()
    length = user32.GetWindowTextLengthW(hwnd)
    buffer = ctypes.create_unicode_buffer(length + 1)
    user32.GetWindowTextW(hwnd, buffer, length + 1)
    return buffer.value


def default_get_active_window_title() -> str:
    if sys.platform == "win32":
        return get_active_window_title_windows()
    raise UnsupportedPlatformError(f"active_window is not supported on {sys.platform}")


def register_active_window_tool(
    registry: ToolRegistry, get_title: Callable[[], str] = default_get_active_window_title
) -> None:
    def _active_window(_arguments: dict[str, Any]) -> str:
        try:
            return json.dumps({"title": get_title()})
        except UnsupportedPlatformError as exc:
            return json.dumps({"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)})

    registry.register(
        Tool(
            name="active_window",
            description="Get the title of the currently focused window.",
            parameters={"type": "object", "properties": {}},
            handler=_active_window,
        )
    )
