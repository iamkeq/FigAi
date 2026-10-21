from __future__ import annotations

import json

import pytest

from figai.agent.tools import Tool, ToolRegistry


def make_registry() -> ToolRegistry:
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="add",
            description="Adds two numbers.",
            parameters={
                "type": "object",
                "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                "required": ["a", "b"],
            },
            handler=lambda args: json.dumps({"sum": args["a"] + args["b"]}),
        )
    )
    return registry


def test_execute_runs_registered_tool() -> None:
    registry = make_registry()

    result = registry.execute("add", json.dumps({"a": 2, "b": 3}))

    assert json.loads(result) == {"sum": 5}


def test_execute_unknown_tool_returns_error_without_raising() -> None:
    registry = make_registry()

    result = registry.execute("does_not_exist", "{}")

    assert "error" in json.loads(result)


def test_execute_invalid_json_arguments_returns_error_without_raising() -> None:
    registry = make_registry()

    result = registry.execute("add", "{not json")

    assert "error" in json.loads(result)


def test_execute_handler_exception_returns_error_without_raising() -> None:
    registry = ToolRegistry()
    registry.register(
        Tool(
            name="boom",
            description="Always fails.",
            parameters={"type": "object", "properties": {}},
            handler=lambda _args: (_ for _ in ()).throw(RuntimeError("kaboom")),
        )
    )

    result = registry.execute("boom", "{}")

    assert "kaboom" in json.loads(result)["error"]


def test_register_duplicate_name_raises() -> None:
    registry = make_registry()

    with pytest.raises(ValueError):
        registry.register(
            Tool(name="add", description="dup", parameters={"type": "object", "properties": {}}, handler=lambda a: "")
        )


def test_to_openai_tools_matches_schema_shape() -> None:
    registry = make_registry()

    schemas = registry.to_openai_tools()

    assert schemas == [
        {
            "type": "function",
            "function": {
                "name": "add",
                "description": "Adds two numbers.",
                "parameters": {
                    "type": "object",
                    "properties": {"a": {"type": "number"}, "b": {"type": "number"}},
                    "required": ["a", "b"],
                },
            },
        }
    ]
