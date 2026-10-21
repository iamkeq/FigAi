"""Pure Slack message-handling logic, decoupled from slack-bolt so it can be
fully unit tested without any real Slack connection."""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from figai.agent.loop import Message
from figai.config import Config
from figai.logging_setup import get_logger
from figai.skills_system.loader import SkillError

logger = get_logger("slack")

RESTART_SENTINEL = "__RESTART__"


def is_authorized_message(event: dict[str, Any], allowed_user_id: str) -> bool:
    if event.get("bot_id"):
        return False
    if event.get("subtype"):
        return False
    return event.get("user") == allowed_user_id


@dataclass
class SlackRouter:
    session: Any  # an AgentSession (or test double exposing .loop and .loader)
    config: Config
    allowed_user_id: str
    start_time: float = field(default_factory=time.monotonic)
    last_error: str | None = None
    conversations: dict[str, list[Message]] = field(default_factory=dict)

    def handle_event(self, event: dict[str, Any]) -> str | None:
        """Entry point for a raw Slack `message` event. Returns the text to send
        back, or None if nothing should be said (wrong sender, empty message)."""
        if not is_authorized_message(event, self.allowed_user_id):
            logger.info("Ignoring message from an unauthorized sender or a bot")
            return None

        text = (event.get("text") or "").strip()
        if not text:
            return None

        return self.handle_text(event.get("channel", "unknown"), text)

    def handle_text(self, channel: str, text: str) -> str | None:
        if text == "!status":
            return self.status_text()
        if text == "!skills":
            return self.skills_text()
        if text.startswith("!approve "):
            return self.approve_skill(text.removeprefix("!approve ").strip())
        if text == "!restart":
            return RESTART_SENTINEL

        conversation = self.conversations.setdefault(channel, [])
        conversation.append({"role": "user", "content": text})
        try:
            reply = self.session.loop.run(conversation)
        except Exception as exc:  # noqa: BLE001 - one bad turn must not kill the bot
            logger.exception("Agent loop failed")
            self.last_error = str(exc)
            conversation.pop()
            return "Something went wrong on my end -- check the logs."

        conversation.append({"role": "assistant", "content": reply})
        return reply

    def status_text(self) -> str:
        uptime_minutes = (time.monotonic() - self.start_time) / 60
        return (
            f"Fig is online. Uptime: {uptime_minutes:.1f} min. "
            f"Model: {self.config.app.models.default_chat}. Last error: {self.last_error or 'none'}"
        )

    def skills_text(self) -> str:
        approved = self.session.loader.discover()
        lines = [f"- {s.name}: {s.description}" for s in approved]
        pending = self.session.loader.list_pending()
        if pending:
            lines.append("Pending approval (!approve <name>): " + ", ".join(pending))
        return "\n".join(lines) if lines else "No skills installed."

    def approve_skill(self, name: str) -> str:
        try:
            self.session.loader.approve(name)
            return f"Approved and activated skill '{name}'. Restart to load any new tools it adds."
        except SkillError as exc:
            return str(exc)
