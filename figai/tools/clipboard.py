"""get_clipboard / set_clipboard: cross-platform via pyperclip."""

from __future__ import annotations

import json
from typing import Any, Callable

import pyperclip

from figai.agent.tools import Tool, ToolRegistry

ClipboardReader = Callable[[], str]
ClipboardWriter = Callable[[str], None]


def register_clipboard_tools(
    registry: ToolRegistry,
    reader: ClipboardReader = pyperclip.paste,
    writer: ClipboardWriter = pyperclip.copy,
) -> None:
    def _get(_arguments: dict[str, Any]) -> str:
        try:
            return json.dumps({"content": reader()})
        except Exception as exc:  # noqa: BLE001 - e.g. no clipboard backend available
            return json.dumps({"error": str(exc)})

    registry.register(
        Tool(
            name="get_clipboard",
            description="Read the current text on the system clipboard.",
            parameters={"type": "object", "properties": {}},
            handler=_get,
        )
    )

    def _set(arguments: dict[str, Any]) -> str:
        try:
            writer(arguments["content"])
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)})
        return json.dumps({"status": "copied"})

    registry.register(
        Tool(
            name="set_clipboard",
            description="Set the system clipboard to the given text.",
            parameters={
                "type": "object",
                "properties": {"content": {"type": "string"}},
                "required": ["content"],
            },
            handler=_set,
        )
    )
