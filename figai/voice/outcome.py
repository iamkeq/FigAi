"""How did a spoken command go? Looks at the tools that ran during it and says whether to play
"All set", "didn't work", or ask for a confirmation -- or stay quiet because it was just a question
(the answer is in your DM; saying "all set" after "what time is it" would be silly)."""

from __future__ import annotations

import json
from typing import Any

READ_ONLY_PREFIXES = (
    "list_", "get_", "read_", "search_", "inspect_", "summarize_", "memory_read", "memory_search", "memory_list",
    "web_search", "fetch_url", "system_status", "network_status", "battery_status", "active_window", "health_check",
    "recent_logs", "version_info", "openrouter_usage", "obs_status", "briefing_status", "diagnose_", "ask_agent",
    "load_skill", "cron_log", "run_check", "propose_",
)


def _is_read_only(tool_name: str) -> bool:
    return tool_name.startswith(READ_ONLY_PREFIXES)


def classify(seen: list[tuple[str, str]]) -> tuple[str, str | None]:
    """("confirm", preview) | ("done", None) | ("fail", None) | ("answer", None)."""
    acted = failed = False
    for name, result in seen:
        try:
            data: Any = json.loads(result)
        except ValueError:
            continue
        if not isinstance(data, dict):
            continue
        if data.get("status") == "awaiting_confirmation":
            return "confirm", data.get("preview")
        inner = data.get("result") if isinstance(data.get("result"), dict) else data  # auto-confirmed results nest
        if inner.get("error"):
            failed = True
        elif not _is_read_only(name):
            acted = True
    if acted:
        return "done", None
    if failed:
        return "fail", None
    return "answer", None
