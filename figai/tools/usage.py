"""openrouter_usage: reports usage and remaining credit limit for Fig's own
OpenRouter API key, via OpenRouter's /api/v1/key endpoint."""

from __future__ import annotations

import json
from typing import Any

import requests

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config

OPENROUTER_KEY_ENDPOINT = "https://openrouter.ai/api/v1/key"
TIMEOUT_SECONDS = 10


class UsageError(Exception):
    pass


def fetch_usage(http: Any, api_key: str) -> dict[str, Any]:
    try:
        response = http.get(
            OPENROUTER_KEY_ENDPOINT, headers={"Authorization": f"Bearer {api_key}"}, timeout=TIMEOUT_SECONDS
        )
        response.raise_for_status()
        return response.json()["data"]
    except Exception as exc:  # noqa: BLE001 - normalize every transport/parsing failure
        raise UsageError(str(exc)) from exc


def register_usage_tool(registry: ToolRegistry, config: Config, http: Any = requests) -> None:
    def _usage(_arguments: dict[str, Any]) -> str:
        try:
            data = fetch_usage(http, config.settings.openrouter_api_key)
        except UsageError as exc:
            return json.dumps({"error": str(exc)})

        return json.dumps(
            {
                "is_free_tier": data.get("is_free_tier"),
                "limit_usd": data.get("limit"),
                "limit_remaining_usd": data.get("limit_remaining"),
                "usage_all_time_usd": data.get("usage"),
                "usage_daily_usd": data.get("usage_daily"),
                "usage_weekly_usd": data.get("usage_weekly"),
                "usage_monthly_usd": data.get("usage_monthly"),
                "free_model_daily_requests": data.get("free_model_daily_requests"),
            }
        )

    registry.register(
        Tool(
            name="openrouter_usage",
            description="Check usage and remaining credit limit for Fig's own OpenRouter API key.",
            parameters={"type": "object", "properties": {}},
            handler=_usage,
        )
    )
