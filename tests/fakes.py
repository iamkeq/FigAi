"""Minimal fakes mimicking the OpenAI SDK's chat.completions.create response shape,
just enough to drive AgentLoop in tests without any network access."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any


def make_tool_call(call_id: str, name: str, arguments: str) -> SimpleNamespace:
    return SimpleNamespace(id=call_id, function=SimpleNamespace(name=name, arguments=arguments))


def make_response(content: str | None = None, tool_calls: list[Any] | None = None) -> SimpleNamespace:
    message = SimpleNamespace(content=content, tool_calls=tool_calls)
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


class FakeCompletions:
    def __init__(self, responses: list[Any]) -> None:
        self._responses = list(responses)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("FakeCompletions.create called more times than responses provided")
        return self._responses.pop(0) if len(self._responses) > 1 else self._responses[0]


class FakeClient:
    def __init__(self, responses: list[Any]) -> None:
        self.chat = SimpleNamespace(completions=FakeCompletions(responses))
