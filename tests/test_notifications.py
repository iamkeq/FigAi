from __future__ import annotations

import json

from figai.agent.tools import ToolRegistry
from figai.tools.notifications import register_notification_tool


def test_send_notification_calls_notifier_with_title_and_message() -> None:
    calls: list[tuple[str, str]] = []
    registry = ToolRegistry()
    register_notification_tool(registry, notifier=lambda title, message: calls.append((title, message)))

    result = json.loads(
        registry.execute("send_notification", json.dumps({"title": "Heads up", "message": "Download finished"}))
    )

    assert result == {"status": "sent"}
    assert calls == [("Heads up", "Download finished")]


def test_send_notification_defaults_title_to_fig() -> None:
    calls: list[tuple[str, str]] = []
    registry = ToolRegistry()
    register_notification_tool(registry, notifier=lambda title, message: calls.append((title, message)))

    registry.execute("send_notification", json.dumps({"message": "hi"}))

    assert calls == [("Fig", "hi")]


def test_send_notification_handles_notifier_failure() -> None:
    def failing_notifier(title: str, message: str) -> None:
        raise RuntimeError("no notification backend")

    registry = ToolRegistry()
    register_notification_tool(registry, notifier=failing_notifier)

    result = json.loads(registry.execute("send_notification", json.dumps({"message": "hi"})))

    assert "error" in result
