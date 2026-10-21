"""Pure Slack message-handling logic, decoupled from slack-bolt so it can be
fully unit tested without any real Slack connection.

DMs always get a reply. In a channel, Fig only replies when explicitly
@-mentioned, and then keeps talking in that thread for any follow-up message
in it -- it ignores ordinary channel chatter it wasn't addressed in."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from figai.agent.loop import Message
from figai.config import Config
from figai.logging_setup import get_logger
from figai.skills_system.loader import SkillError

logger = get_logger("slack")

RESTART_SENTINEL = "__RESTART__"
MENTION_PATTERN = re.compile(r"<@[A-Z0-9]+>")
Hook = Callable[[], None] | None


def is_authorized_message(event: dict[str, Any], allowed_user_id: str) -> bool:
    if event.get("bot_id"):
        return False
    if event.get("subtype"):
        return False
    return event.get("user") == allowed_user_id


def strip_mentions(text: str) -> str:
    return MENTION_PATTERN.sub("", text).strip()


@dataclass
class SlackReply:
    text: str
    thread_ts: str | None = None


@dataclass
class SlackRouter:
    session: Any  # an AgentSession (or test double exposing .loop and .loader)
    config: Config
    allowed_user_id: str
    start_time: float = field(default_factory=time.monotonic)
    last_error: str | None = None
    conversations: dict[str, list[Message]] = field(default_factory=dict)
    active_threads: set[tuple[str, str]] = field(default_factory=set)

    def handle_event(self, event: dict[str, Any], on_start: Hook = None, on_done: Hook = None) -> SlackReply | None:
        """Entry point for a raw Slack `message` event: always for a DM, or for a
        channel/group message inside a thread Fig is already engaged in.

        `on_start`/`on_done` fire only once Fig has committed to actually
        responding -- never for a message it's going to ignore -- so a
        front-end can use them to show working/done feedback (e.g. a Slack
        reaction) without ever leaving one stuck on an ignored message."""
        if not is_authorized_message(event, self.allowed_user_id):
            logger.info("Ignoring message from an unauthorized sender or a bot")
            return None

        text = (event.get("text") or "").strip()
        if not text:
            return None

        channel = event.get("channel", "unknown")

        if event.get("channel_type") == "im":
            return self._dispatch(channel, text, on_start, on_done, channel=channel)

        thread_ts = event.get("thread_ts")
        if thread_ts is None or (channel, thread_ts) not in self.active_threads:
            return None  # ambient channel chatter Fig wasn't addressed in

        return self._dispatch(f"{channel}:{thread_ts}", text, on_start, on_done, channel=channel, thread_ts=thread_ts)

    def handle_app_mention(
        self, event: dict[str, Any], on_start: Hook = None, on_done: Hook = None
    ) -> SlackReply | None:
        """Entry point for an `app_mention` event: the start of (or a top-level
        reply within) a channel conversation. Always replies in a thread."""
        if not is_authorized_message(event, self.allowed_user_id):
            logger.info("Ignoring app_mention from an unauthorized sender")
            return None

        channel = event.get("channel", "unknown")
        thread_ts = event.get("thread_ts") or event.get("ts")
        if thread_ts is None:
            return None

        self.active_threads.add((channel, thread_ts))

        text = strip_mentions(event.get("text") or "")
        if not text:
            text = "(you mentioned me with no message)"

        return self._dispatch(f"{channel}:{thread_ts}", text, on_start, on_done, channel=channel, thread_ts=thread_ts)

    def _dispatch(
        self,
        conversation_key: str,
        text: str,
        on_start: Hook,
        on_done: Hook,
        channel: str,
        thread_ts: str | None = None,
    ) -> SlackReply:
        context = getattr(self.session, "conversation_context", None)
        if context is not None:
            context.current_channel = channel
            context.current_thread_ts = thread_ts

        if on_start:
            on_start()
        reply_text = self.handle_text(conversation_key, text)
        if on_done:
            on_done()
        return SlackReply(reply_text, thread_ts=thread_ts)

    def handle_text(self, conversation_key: str, text: str) -> str | None:
        if text == "!status":
            return self.status_text()
        if text == "!skills":
            return self.skills_text()
        if text.startswith("!approve "):
            return self.approve_skill(text.removeprefix("!approve ").strip())
        if text == "!restart":
            return RESTART_SENTINEL

        conversation = self.conversations.setdefault(conversation_key, [])
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
