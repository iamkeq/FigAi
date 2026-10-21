"""open_url: opens a URL in the default browser or Chrome specifically."""

from __future__ import annotations

import json
import webbrowser
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry

Opener = Callable[[str, str], None]


def default_opener(url: str, browser: str) -> None:
    if browser == "chrome":
        webbrowser.get("chrome").open(url)
    else:
        webbrowser.open(url)


def register_open_url_tool(registry: ToolRegistry, opener: Opener = default_opener) -> None:
    def _open_url(arguments: dict[str, Any]) -> str:
        url = arguments["url"]
        if not (url.startswith("http://") or url.startswith("https://")):
            return json.dumps({"error": "Only http(s) URLs are allowed"})

        browser = arguments.get("browser", "default")
        try:
            opener(url, browser)
        except Exception as exc:  # noqa: BLE001 - e.g. webbrowser.Error if Chrome isn't registered
            return json.dumps({"error": str(exc)})

        return json.dumps({"status": "opened", "url": url, "browser": browser})

    registry.register(
        Tool(
            name="open_url",
            description="Open a URL in the user's default browser, or Chrome specifically.",
            parameters={
                "type": "object",
                "properties": {
                    "url": {"type": "string"},
                    "browser": {"type": "string", "enum": ["default", "chrome"]},
                },
                "required": ["url"],
            },
            handler=_open_url,
        )
    )
