from __future__ import annotations

import json

from figai.agent.tools import ToolRegistry
from figai.tools.clipboard import register_clipboard_tools


def test_get_clipboard_returns_reader_output() -> None:
    registry = ToolRegistry()
    register_clipboard_tools(registry, reader=lambda: "hello clipboard", writer=lambda text: None)

    result = json.loads(registry.execute("get_clipboard", "{}"))

    assert result == {"content": "hello clipboard"}


def test_set_clipboard_calls_writer_with_content() -> None:
    written: list[str] = []
    registry = ToolRegistry()
    register_clipboard_tools(registry, reader=lambda: "", writer=written.append)

    result = json.loads(registry.execute("set_clipboard", json.dumps({"content": "copy me"})))

    assert result == {"status": "copied"}
    assert written == ["copy me"]


def test_get_clipboard_handles_backend_failure() -> None:
    def failing_reader() -> str:
        raise RuntimeError("no clipboard backend")

    registry = ToolRegistry()
    register_clipboard_tools(registry, reader=failing_reader, writer=lambda text: None)

    result = json.loads(registry.execute("get_clipboard", "{}"))

    assert "error" in result


def test_set_clipboard_handles_backend_failure() -> None:
    def failing_writer(text: str) -> None:
        raise RuntimeError("no clipboard backend")

    registry = ToolRegistry()
    register_clipboard_tools(registry, reader=lambda: "", writer=failing_writer)

    result = json.loads(registry.execute("set_clipboard", json.dumps({"content": "x"})))

    assert "error" in result
