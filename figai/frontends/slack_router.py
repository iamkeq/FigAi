"""Pure Slack message-handling logic, decoupled from slack-bolt so it can be
fully unit tested without any real Slack connection.

DMs always get a reply. In a channel, Fig only replies when explicitly
@-mentioned, and then keeps talking in that thread for any follow-up message
in it -- it ignores ordinary channel chatter it wasn't addressed in."""

from __future__ import annotations

import contextlib
import json
import re
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from figai.agent.history import is_idle, trim_history
from figai.agent.loop import Message
from figai.config import Config
from figai import help as help_menu
from figai.linkify import shorten_links
from figai.logging_setup import get_logger
from figai.skills_system.loader import SkillError

logger = get_logger("slack")

RESTART_SENTINEL = "__RESTART__"
# "update yourself -y" (also --yes, or just !update) skips the confirmation round trip. Matched in
# code, on the owner's own message, so it never depends on the model deciding to skip the check.
UPDATE_NOW_PATTERN = re.compile(r"^(?:!update|(?:update yourself|update fig)\s+(?:-y|--yes))$", re.IGNORECASE)
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
    last_active: dict[str, float] = field(default_factory=dict)
    clock: Callable[[], float] = time.time
    active_threads: set[tuple[str, str]] = field(default_factory=set)
    on_change: Hook = None

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
        with getattr(self.session, "run_lock", None) or contextlib.nullcontext():
            context = getattr(self.session, "conversation_context", None)
            if context is not None:
                context.current_channel = channel
                context.current_thread_ts = thread_ts

            if on_start:
                on_start()
            reply_text = self.handle_text(conversation_key, text)
            if on_done:
                on_done()
        if self.on_change:
            self.on_change()
        return SlackReply(shorten_links(reply_text), thread_ts=thread_ts)

    def to_state(self) -> dict[str, Any]:
        return {
            "active_threads": [list(pair) for pair in self.active_threads],
            "conversations": self.conversations,
            "last_active": self.last_active,
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        self.active_threads = {tuple(pair) for pair in state.get("active_threads", [])}
        self.conversations = state.get("conversations", {})
        self.last_active = state.get("last_active", {})

    def handle_text(self, conversation_key: str, text: str) -> str | None:
        if text == "!status":
            return self.status_text()
        if text == "!health":
            return self.session.health.report()
        if text == "!logs" or text.startswith("!logs ") or text == "!errors":
            return self.logs_text(text)
        if UPDATE_NOW_PATTERN.match(text):
            return self.update_now()
        if text == "!downloads" or text.startswith("!downloads "):
            return self.downloads_text(text.removeprefix("!downloads").strip().lower())
        if text == "!tools" or text.startswith("!tools "):
            return self.tools_text(text.removeprefix("!tools").strip())
        if text == "!help" or text.startswith("!help "):
            return self.help_text(text.removeprefix("!help").strip())
        if text == "!briefing" or text.startswith("!briefing "):
            return self.briefing_command(text.removeprefix("!briefing").strip())
        if text == "!checks":
            checks = self.session.checks.list()
            return "\n".join(self.session.checks.describe(c) for c in checks) or "No scheduled checks."
        if text == "!skills":
            return self.skills_text()
        if text.startswith("!approve "):
            return self.approve_skill(text.removeprefix("!approve ").strip())
        if text == "!restart":
            return RESTART_SENTINEL
        if text == "!model" or text.startswith("!model "):
            return self.model_command(text.removeprefix("!model").strip())

        if text in ("!new", "!reset"):
            self.conversations.pop(conversation_key, None)
            return "Fresh start -- I've forgotten this conversation."

        settings = self.config.app.conversation
        now = self.clock()
        is_dm = ":" not in conversation_key  # channel threads are keyed "channel:thread_ts"
        if is_dm and is_idle(self.last_active.get(conversation_key), now, settings.dm_idle_minutes):
            logger.info("DM conversation idle for over %s min -- starting fresh", settings.dm_idle_minutes)
            self.conversations.pop(conversation_key, None)
        self.last_active[conversation_key] = now

        conversation = self.conversations.setdefault(conversation_key, [])
        conversation.append({"role": "user", "content": text})
        trim_history(conversation, settings.max_messages)
        try:
            reply = self.session.loop.run(conversation)
        except Exception as exc:  # noqa: BLE001 - one bad turn must not kill the bot
            logger.exception("Agent loop failed")
            self.last_error = str(exc)
            conversation.pop()
            return "Something went wrong on my end -- check the logs."

        conversation.append({"role": "assistant", "content": reply})
        return reply

    def model_command(self, argument: str) -> str:
        """`!model` shows the models, `!model reset` restores the default chat
        model, `!model <id>` switches it. Deterministic (no model involved), so it
        still works when the current chat model is the problem."""
        from figai.tools.models import ModelError

        manager = getattr(self.session, "models", None)
        if manager is None:
            return "Model switching isn't available."
        try:
            if not argument:
                return manager.describe()
            if argument == "reset":
                return manager.reset("chat")
            return manager.set("chat", argument)
        except ModelError as exc:
            return str(exc)

    def status_text(self) -> str:
        uptime_minutes = (time.monotonic() - self.start_time) / 60
        return (
            f"Fig is online. Uptime: {uptime_minutes:.1f} min. "
            f"Model: {self.config.app.models.default_chat}. Last error: {self.last_error or 'none'}"
        )

    def update_now(self) -> str:
        """Runs the same propose -> resolve steps as "update yourself", back to back."""
        tools = self.session.loop.tools
        try:
            proposed = json.loads(tools.execute("propose_update_fig", "{}"))
            result = json.loads(tools.execute("resolve_update_fig", json.dumps({"token": proposed["token"]})))
        except (KeyError, ValueError) as exc:
            return f"Couldn't start the update: {exc}"
        if result.get("status") == "updated":
            return "Pulled the latest code. Restarting in a few seconds..."
        return str(result.get("error", result))

    def help_text(self, query: str) -> str:
        """`!help` is the menu; `!help email` one topic; `!help all` everything."""
        try:
            body = self.session.loader.get_instructions("help")
        except SkillError:
            return "The help skill isn't installed."
        return help_menu.topic(body, query) if query.strip() else help_menu.menu(body)

    def downloads_text(self, argument: str) -> str:
        """`!downloads` -- what's downloading now (SABnzbd, Sonarr, Radarr on turbo-plex); `!downloads recent`."""
        tools = getattr(getattr(self.session, "loop", None), "tools", None)
        if tools is None:
            return "I can't reach my media tools right now."
        name = "media_recent" if argument in ("recent", "history", "done", "finished") else "media_downloads"
        try:
            data = json.loads(tools.execute(name, "{}"))
        except ValueError:
            return "I couldn't read the media server's answer."
        return data.get("summary") or data.get("error", "No answer from the media server.")

    def tools_text(self, query: str) -> str:
        tools = getattr(getattr(self.session, "loop", None), "tools", None)
        if tools is None:
            return "I can't list my tools right now."
        return help_menu.tools_list(help_menu.describe_registry(tools), query)

    def briefing_command(self, argument: str) -> str:
        """`!briefing` (status), `on`, `off`, `now`, `time HH:MM`, `email on|off`."""
        manager = self.session.briefing
        words = argument.lower().split()
        try:
            if not words:
                return manager.status()
            if words[0] == "on":
                manager.configure(enabled=True)
            elif words[0] == "off":
                manager.configure(enabled=False)
            elif words[0] == "now":
                manager.send_now()
                return "Sent."
            elif words[0] == "time" and len(words) == 2:
                manager.configure(time=words[1])
            elif words[0] == "email" and len(words) == 2 and words[1] in ("on", "off"):
                manager.configure(include_email=words[1] == "on")
            else:
                return "Usage: `!briefing` | `on` | `off` | `now` | `time 07:30` | `email on|off`"
        except ValueError as exc:
            return str(exc)
        return manager.status()

    def logs_text(self, command: str) -> str:
        """`!logs [errors|warnings|tools|all] [count]`; `!errors` is `!logs errors`."""
        from figai.logs_reader import FILTERS, recent_lines

        words = command.split()[1:] if command.startswith("!logs") else []
        kind = next((w for w in words if w in FILTERS), "errors")
        count = next((int(w) for w in words if w.isdigit()), 10)
        lines = recent_lines(kind, count)
        if not lines:
            return f"Nothing in the log matches '{kind}'."
        return f"Last {len(lines)} {kind} log record(s):\n```\n" + "\n".join(lines) + "\n```"

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
