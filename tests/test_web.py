from __future__ import annotations

import json

from figai.agent.tools import ToolRegistry
from figai.tools.web import register_open_url_tool


def test_open_url_calls_opener_with_default_browser() -> None:
    calls: list[tuple[str, str]] = []
    registry = ToolRegistry()
    register_open_url_tool(registry, opener=lambda url, browser: calls.append((url, browser)))

    result = json.loads(registry.execute("open_url", json.dumps({"url": "https://example.com"})))

    assert result == {"status": "opened", "url": "https://example.com", "browser": "default"}
    assert calls == [("https://example.com", "default")]


def test_open_url_passes_through_chrome_choice() -> None:
    calls: list[tuple[str, str]] = []
    registry = ToolRegistry()
    register_open_url_tool(registry, opener=lambda url, browser: calls.append((url, browser)))

    registry.execute("open_url", json.dumps({"url": "https://example.com", "browser": "chrome"}))

    assert calls == [("https://example.com", "chrome")]


def test_open_url_rejects_non_http_scheme() -> None:
    registry = ToolRegistry()
    register_open_url_tool(registry, opener=lambda url, browser: None)

    result = json.loads(registry.execute("open_url", json.dumps({"url": "file:///etc/passwd"})))

    assert "error" in result


def test_open_url_handles_opener_failure_gracefully() -> None:
    def failing_opener(url: str, browser: str) -> None:
        raise RuntimeError("no browser found")

    registry = ToolRegistry()
    register_open_url_tool(registry, opener=failing_opener)

    result = json.loads(registry.execute("open_url", json.dumps({"url": "https://example.com"})))

    assert "error" in result
