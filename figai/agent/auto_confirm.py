"""Skips the "are you sure?" round trip for things you asked for out loud.

Speaking a command is already deliberate, and a spoken "yes" needs the wake word all over
again, so during a voice command the ordinary actions (post to Slack, send a file, move a
note, update Fig, add a movie) simply happen. Anything destructive or open-ended still asks:
deleting or overwriting notes, commands on your servers, and changes to Fig's settings.
This is done in code on the tool result, not by telling the model to skip the check, so
nothing the model reads can switch it on -- only a voice command from you does."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from typing import Any, Callable, Iterator

from figai.logging_setup import get_logger
from figai.tools.confirm import ConfirmationStore

logger = get_logger("auto_confirm")

# action type -> the tool that carries it out. Types missing from this map always ask.
RESOLVERS = {
    "slack_post": "resolve_slack_post",
    "send_file": "resolve_send_file",
    "update_fig": "resolve_update_fig",
    "add_media": "resolve_add_media",
    "memory_move": "resolve_memory_change",
}
# Never auto-confirmed, whatever the setting: memory_delete, memory_overwrite, ssh_run, config_change.


class AutoConfirm:
    def __init__(self, confirmations: ConfirmationStore, enabled: bool = True) -> None:
        self._confirmations = confirmations
        self.enabled = enabled
        self._local = threading.local()

    @contextmanager
    def voice_turn(self) -> Iterator[None]:
        """Within this block (one spoken command, on this thread) ordinary confirmations are skipped."""
        previous = getattr(self._local, "active", False)
        self._local.active = self.enabled
        try:
            yield
        finally:
            self._local.active = previous

    def hook(self, execute: Callable[[str, str], str]) -> Callable[[str, str], str | None]:
        """A ToolRegistry result hook. `execute` runs a tool by name with JSON arguments."""

        def _after(tool_name: str, result: str) -> str | None:
            if not getattr(self._local, "active", False):
                return None
            try:
                parsed: Any = json.loads(result)
            except ValueError:
                return None
            if not isinstance(parsed, dict) or parsed.get("status") != "awaiting_confirmation":
                return None
            token = parsed.get("token")
            action_type = self._confirmations.peek_type(token) if isinstance(token, str) else None
            resolver = RESOLVERS.get(action_type or "")
            if resolver is None:
                return None  # destructive or unknown: the normal ask-first flow stands
            logger.info("Auto-confirming %s (%s) because it was asked for by voice", action_type, tool_name)
            outcome = execute(resolver, json.dumps({"token": token}))
            try:
                outcome_data: Any = json.loads(outcome)
            except ValueError:
                outcome_data = outcome
            return json.dumps(
                {
                    "auto_confirmed": "The user asked for this out loud, so it was carried out without a second yes.",
                    "preview": parsed.get("preview"),
                    "result": outcome_data,
                }
            )

        return _after
