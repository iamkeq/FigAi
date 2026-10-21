from __future__ import annotations

import json

from figai.agent.tools import ToolRegistry
from figai.tools.usage import register_usage_tool


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

    def get(self, url, *, headers, timeout):
        self.calls.append({"url": url, "headers": headers, "timeout": timeout})
        return self._response


class FakeConfig:
    class settings:  # noqa: N801 - mimics Config.settings access pattern
        openrouter_api_key = "sk-or-v1-test-key"


def test_openrouter_usage_returns_summarized_fields() -> None:
    http = FakeHttp(
        FakeResponse(
            {
                "data": {
                    "is_free_tier": True,
                    "limit": None,
                    "limit_remaining": None,
                    "usage": 1.23,
                    "usage_daily": 0.1,
                    "usage_weekly": 0.5,
                    "usage_monthly": 1.0,
                    "free_model_daily_requests": {"used": 5, "limit": 50, "remaining": 45},
                }
            }
        )
    )
    registry = ToolRegistry()
    register_usage_tool(registry, FakeConfig(), http=http)

    result = json.loads(registry.execute("openrouter_usage", "{}"))

    assert result == {
        "is_free_tier": True,
        "limit_usd": None,
        "limit_remaining_usd": None,
        "usage_all_time_usd": 1.23,
        "usage_daily_usd": 0.1,
        "usage_weekly_usd": 0.5,
        "usage_monthly_usd": 1.0,
        "free_model_daily_requests": {"used": 5, "limit": 50, "remaining": 45},
    }


def test_openrouter_usage_sends_bearer_auth_header() -> None:
    http = FakeHttp(FakeResponse({"data": {}}))
    registry = ToolRegistry()
    register_usage_tool(registry, FakeConfig(), http=http)

    registry.execute("openrouter_usage", "{}")

    assert http.calls[0]["headers"] == {"Authorization": "Bearer sk-or-v1-test-key"}


def test_openrouter_usage_returns_error_on_http_failure_without_raising() -> None:
    http = FakeHttp(FakeResponse(None, status_ok=False))
    registry = ToolRegistry()
    register_usage_tool(registry, FakeConfig(), http=http)

    result = json.loads(registry.execute("openrouter_usage", "{}"))

    assert "error" in result
