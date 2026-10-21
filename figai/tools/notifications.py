"""send_notification: a native OS notification. Real implementation on Windows
(winotify) and macOS (osascript, for local dev/testing); Linux falls back to
`notify-send` if present."""

from __future__ import annotations

import json
import subprocess
import sys
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry

Notifier = Callable[[str, str], None]


def _notify_windows(title: str, message: str) -> None:
    from winotify import Notification  # type: ignore[import-not-found]

    Notification(app_id="FigAI", title=title, msg=message).show()


def _notify_macos(title: str, message: str) -> None:
    script = f'display notification {message!r} with title {title!r}'
    subprocess.run(["osascript", "-e", script], check=True, capture_output=True)


def _notify_linux(title: str, message: str) -> None:
    subprocess.run(["notify-send", title, message], check=True, capture_output=True)


def default_notifier(title: str, message: str) -> None:
    if sys.platform == "win32":
        _notify_windows(title, message)
    elif sys.platform == "darwin":
        _notify_macos(title, message)
    else:
        _notify_linux(title, message)


def register_notification_tool(registry: ToolRegistry, notifier: Notifier = default_notifier) -> None:
    def _send(arguments: dict[str, Any]) -> str:
        title = arguments.get("title", "Fig")
        message = arguments["message"]
        try:
            notifier(title, message)
        except Exception as exc:  # noqa: BLE001 - missing notify-send, winotify, etc.
            return json.dumps({"error": str(exc)})
        return json.dumps({"status": "sent"})

    registry.register(
        Tool(
            name="send_notification",
            description="Show a native desktop notification on this machine.",
            parameters={
                "type": "object",
                "properties": {"title": {"type": "string"}, "message": {"type": "string"}},
                "required": ["message"],
            },
            handler=_send,
        )
    )
