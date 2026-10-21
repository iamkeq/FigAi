"""web_search: a standalone OpenRouter completion with the web search plugin
enabled, exposed as a tool so the main agent can look things up mid-turn.

Unlike every other tool in Fig, this costs real money per call -- OpenRouter
bills for web search (via Exa) even against a free model."""

from __future__ import annotations

import json
from typing import Any

import requests

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config

CHAT_COMPLETIONS_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT_SECONDS = 30
DEFAULT_MAX_RESULTS = 5


def register_web_search_tool(registry: ToolRegistry, config: Config, http: Any = requests) -> None:
    def _search(arguments: dict[str, Any]) -> str:
        query = arguments["query"]
        max_results = int(arguments.get("max_results", DEFAULT_MAX_RESULTS))

        payload = {
            "model": config.app.models.default_chat,
            "messages": [{"role": "user", "content": query}],
            "plugins": [{"id": "web", "max_results": max_results}],
        }
        try:
            response = http.post(
                CHAT_COMPLETIONS_ENDPOINT,
                headers={"Authorization": f"Bearer {config.settings.openrouter_api_key}"},
                json=payload,
                timeout=TIMEOUT_SECONDS,
            )
            response.raise_for_status()
            data = response.json()
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": str(exc)})

        message = data.get("choices", [{}])[0].get("message", {})
        sources = [
            {"title": a["url_citation"].get("title"), "url": a["url_citation"].get("url")}
            for a in (message.get("annotations") or [])
            if a.get("type") == "url_citation" and "url_citation" in a
        ]
        return json.dumps({"answer": message.get("content", ""), "sources": sources})

    registry.register(
        Tool(
            name="web_search",
            description=(
                "Search the web for current information and get a summarized answer with sources. "
                "This costs real OpenRouter credit per call (unlike every other tool) -- only use it "
                "when the answer genuinely needs current or external information you don't already have."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "max_results": {"type": "integer", "description": "How many sources to consider (default 5)."},
                },
                "required": ["query"],
            },
            handler=_search,
        )
    )
