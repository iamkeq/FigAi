"""health_check and recent_logs: lets Fig (and you, through him) inspect his own state."""

from __future__ import annotations

import json
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry
from figai.logs_reader import FILTERS, recent_lines


def register_diagnostics_tools(registry: ToolRegistry, health_report: Callable[[], str]) -> None:
    registry.register(
        Tool(
            name="health_check",
            description=(
                "Check whether everything Fig depends on is working: OpenRouter key, the remote vault, Gmail, "
                "the Chrome bridge, and recent errors. Use when asked 'are you ok?', 'is everything working?', "
                "or while troubleshooting."
            ),
            parameters={"type": "object", "properties": {}},
            handler=lambda _a: json.dumps({"report": health_report()}),
        )
    )

    def _logs(arguments: dict[str, Any]) -> str:
        kind = arguments.get("kind", "errors")
        if kind not in FILTERS:
            return json.dumps({"error": f"kind must be one of {', '.join(FILTERS)}"})
        lines = recent_lines(kind, int(arguments.get("count", 15)))
        return json.dumps({"kind": kind, "lines": lines or ["(nothing matched)"]})

    registry.register(
        Tool(
            name="recent_logs",
            description=(
                "Show recent lines from Fig's own log. kind: 'errors' (default), 'warnings', 'tools' (tool calls "
                "and results), or 'all'. count: how many records (default 15, max 50). Read-only; never contains secrets."
            ),
            parameters={
                "type": "object",
                "properties": {"kind": {"type": "string", "enum": list(FILTERS)}, "count": {"type": "integer"}},
            },
            handler=_logs,
        )
    )
