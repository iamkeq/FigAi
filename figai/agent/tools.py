"""Tool registry: defines, schemas, and safely dispatches native tool calls."""

from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Callable, Iterator

from figai.logging_setup import get_logger

logger = get_logger("tools")

ToolHandler = Callable[[dict[str, Any]], str]


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: ToolHandler

    def to_openai_schema(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        # Optional (tool name, result) -> replacement result; see figai/agent/auto_confirm.py.
        self.result_hook: Callable[[str, str], str | None] | None = None
        self._observers: list[list[tuple[str, str]]] = []

    def register(self, tool: Tool) -> None:
        if tool.name in self._tools:
            raise ValueError(f"Tool already registered: {tool.name}")
        self._tools[tool.name] = tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    @contextmanager
    def observe(self) -> Iterator[list[tuple[str, str]]]:
        """Collects (tool name, result) for every tool run inside the block -- used to tell how a
        spoken command went (did it act? fail? ask for a confirmation?)."""
        seen: list[tuple[str, str]] = []
        self._observers.append(seen)
        try:
            yield seen
        finally:
            self._observers.remove(seen)

    def describe(self) -> list[tuple[str, str]]:
        """[(name, description)] for every registered tool."""
        return [(tool.name, tool.description) for tool in self._tools.values()]

    def to_openai_tools(self) -> list[dict[str, Any]]:
        return [tool.to_openai_schema() for tool in self._tools.values()]

    def execute(self, name: str, arguments_json: str) -> str:
        """Runs a tool by name. Never raises -- tool failures become a JSON error
        string fed back to the model, so one bad call can't crash the agent loop."""
        result = self._execute(name, arguments_json)
        for seen in self._observers:
            seen.append((name, result))
        return result

    def _execute(self, name: str, arguments_json: str) -> str:
        tool = self._tools.get(name)
        if tool is None:
            logger.error("Model requested unknown tool: %s", name)
            return json.dumps({"error": f"Unknown tool: {name}"})

        try:
            arguments = json.loads(arguments_json) if arguments_json else {}
        except json.JSONDecodeError:
            logger.error("Tool %s called with invalid JSON arguments", name)
            return json.dumps({"error": "Invalid JSON arguments"})

        logger.info("Executing tool %s args=%s", name, _truncate(json.dumps(arguments)))
        try:
            result = tool.handler(arguments)
        except Exception as exc:  # noqa: BLE001 - tool failures must not crash the loop
            logger.exception("Tool %s raised an error", name)
            return json.dumps({"error": str(exc)})

        logger.info("Tool %s result=%s", name, _truncate(str(result)))
        if self.result_hook is not None:
            try:
                replacement = self.result_hook(name, result)
            except Exception:  # noqa: BLE001 - a broken hook must not lose the tool's own result
                logger.exception("Result hook failed for %s", name)
                replacement = None
            if replacement is not None:
                logger.info("Tool %s result replaced by hook: %s", name, _truncate(replacement))
                result = replacement
        return result


def _truncate(value: str, limit: int = 300) -> str:
    return value if len(value) <= limit else value[:limit] + "..."
