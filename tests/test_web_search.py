from __future__ import annotations

import json

from figai.agent.tools import ToolRegistry
from figai.tools.web_search import register_web_search_tool


class FakeResponse:
    def __init__(self, data, status_ok: bool = True) -> None:
        self._data = data
        self._status_ok = status_ok

    def raise_for_status(self) -> None:
        if not self._status_ok:
            raise RuntimeError("HTTP error")

    def json(self):
        return self._data


class FakeHttp:
    def __init__(self, response: FakeResponse) -> None:
        self._response = response
        self.calls: list[dict] = []

    def post(self, url, *, headers, json, timeout):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        return self._response


class FakeConfig:
    class settings:  # noqa: N801
        openrouter_api_key = "sk-or-v1-test"

    class app:
        class models:
            default_chat = "test/model"


def test_web_search_returns_answer_and_sources() -> None:
    http = FakeHttp(
        FakeResponse(
            {
                "choices": [
                    {
                        "message": {
                            "content": "The sky is blue because of Rayleigh scattering.",
                            "annotations": [
                                {
                                    "type": "url_citation",
                                    "url_citation": {"title": "Why is the sky blue", "url": "https://example.com/sky"},
                                }
                            ],
                        }
                    }
                ]
            }
        )
    )
    registry = ToolRegistry()
    register_web_search_tool(registry, FakeConfig(), http=http)

    result = json.loads(registry.execute("web_search", json.dumps({"query": "why is the sky blue"})))

    assert result == {
        "answer": "The sky is blue because of Rayleigh scattering.",
        "sources": [{"title": "Why is the sky blue", "url": "https://example.com/sky"}],
    }


def test_web_search_sends_web_plugin_and_bearer_auth() -> None:
    http = FakeHttp(FakeResponse({"choices": [{"message": {"content": "x"}}]}))
    registry = ToolRegistry()
    register_web_search_tool(registry, FakeConfig(), http=http)

    registry.execute("web_search", json.dumps({"query": "test"}))

    call = http.calls[0]
    assert call["headers"] == {"Authorization": "Bearer sk-or-v1-test"}
    assert call["json"]["plugins"] == [{"id": "web", "max_results": 5}]
    assert call["json"]["model"] == "test/model"


def test_web_search_respects_custom_max_results() -> None:
    http = FakeHttp(FakeResponse({"choices": [{"message": {"content": "x"}}]}))
    registry = ToolRegistry()
    register_web_search_tool(registry, FakeConfig(), http=http)

    registry.execute("web_search", json.dumps({"query": "test", "max_results": 2}))

    assert http.calls[0]["json"]["plugins"] == [{"id": "web", "max_results": 2}]


def test_web_search_returns_error_on_http_failure_without_raising() -> None:
    http = FakeHttp(FakeResponse(None, status_ok=False))
    registry = ToolRegistry()
    register_web_search_tool(registry, FakeConfig(), http=http)

    result = json.loads(registry.execute("web_search", json.dumps({"query": "test"})))

    assert "error" in result


def test_web_search_handles_response_with_no_annotations() -> None:
    http = FakeHttp(FakeResponse({"choices": [{"message": {"content": "plain answer"}}]}))
    registry = ToolRegistry()
    register_web_search_tool(registry, FakeConfig(), http=http)

    result = json.loads(registry.execute("web_search", json.dumps({"query": "test"})))

    assert result == {"answer": "plain answer", "sources": []}
