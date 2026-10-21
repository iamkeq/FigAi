"""Slack Socket Mode front-end: thin glue wiring slack-bolt to SlackRouter and the
shared agent core. Responds only to the configured owner; everyone else is ignored."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any, Callable

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

from figai.agent.session import AgentSession
from figai.config import Config
from figai.frontends.slack_router import RESTART_SENTINEL, SlackReply, SlackRouter
from figai.logging_setup import get_logger
from figai.persistence.store import JsonStore

logger = get_logger("slack.bot")

REACTION_WORKING = "eyes"
REACTION_DONE = "white_check_mark"


def build_file_sender(app: App, channel: str):
    def _send(path: Path) -> None:
        app.client.files_upload_v2(channel=channel, file=str(path), filename=path.name)

    return _send


def build_message_sender(app: App):
    def _send(channel: str, text: str, thread_ts: str | None) -> None:
        if thread_ts:
            app.client.chat_postMessage(channel=channel, text=text, thread_ts=thread_ts)
        else:
            app.client.chat_postMessage(channel=channel, text=text)

    return _send


def build_channel_lister(app: App):
    def _list() -> list[dict[str, str]]:
        channels: list[dict[str, str]] = []
        cursor: str | None = None
        while True:
            result = app.client.conversations_list(
                types="public_channel,private_channel", limit=200, cursor=cursor, exclude_archived=True
            )
            channels.extend({"id": c["id"], "name": c["name"]} for c in result.get("channels", []))
            cursor = (result.get("response_metadata") or {}).get("next_cursor") or None
            if not cursor:
                break
        return channels

    return _list


def _add_reaction(app: App, channel: str, ts: str, name: str) -> None:
    try:
        app.client.reactions_add(channel=channel, timestamp=ts, name=name)
    except Exception:
        logger.exception("Failed to add :%s: reaction", name)


def _swap_reaction(app: App, channel: str, ts: str, old: str, new: str) -> None:
    try:
        app.client.reactions_remove(channel=channel, timestamp=ts, name=old)
    except Exception:
        logger.exception("Failed to remove :%s: reaction", old)
    try:
        app.client.reactions_add(channel=channel, timestamp=ts, name=new)
    except Exception:
        logger.exception("Failed to add :%s: reaction", new)


def run_slack_bot(config: Config, on_ready: Callable[[SlackRouter], None] | None = None) -> None:
    app = App(token=config.settings.slack_bot_token)
    owner_channel = config.settings.allowed_slack_user_id

    session = AgentSession(
        config,
        file_sender=build_file_sender(app, owner_channel),
        message_sender=build_message_sender(app),
        channel_lister=build_channel_lister(app),
    )
    router = SlackRouter(session=session, config=config, allowed_user_id=owner_channel)

    store = JsonStore(Path(config.app.state_path))
    state = store.load()
    router.restore_state(state.get("slack", {}))
    session.reminder_scheduler.restore(state.get("reminders", []))

    def _save_state() -> None:
        try:
            store.save({"slack": router.to_state(), "reminders": session.reminder_scheduler.to_state()})
        except Exception:
            logger.exception("Failed to persist state to %s", store.path)

    router.on_change = _save_state
    session.reminder_scheduler.on_change = _save_state

    if state:
        logger.info(
            "Restored %d active thread(s) and %d pending reminder(s) from %s",
            len(router.active_threads),
            len(session.reminder_scheduler.list_pending()),
            store.path,
        )

    if on_ready is not None:
        on_ready(router)

    def _dispatch(reply: SlackReply | None, say: Any) -> None:
        if reply is None:
            return
        if reply.text == RESTART_SENTINEL:
            say(text="Restarting...", thread_ts=reply.thread_ts) if reply.thread_ts else say("Restarting...")
            logger.info("Restart requested via Slack")
            os.execv(sys.executable, [sys.executable, "-m", "figai"])
            return
        if reply.thread_ts:
            say(text=reply.text, thread_ts=reply.thread_ts)
        else:
            say(reply.text)

    def _reaction_hooks(channel: str, ts: str) -> tuple[Any, Any]:
        on_start = lambda: _add_reaction(app, channel, ts, REACTION_WORKING)  # noqa: E731
        on_done = lambda: _swap_reaction(app, channel, ts, REACTION_WORKING, REACTION_DONE)  # noqa: E731
        return on_start, on_done

    @app.event("message")
    def handle_message(event: dict[str, Any], say: Any) -> None:
        on_start, on_done = _reaction_hooks(event.get("channel", ""), event.get("ts", ""))
        _dispatch(router.handle_event(event, on_start=on_start, on_done=on_done), say)

    @app.event("app_mention")
    def handle_app_mention(event: dict[str, Any], say: Any) -> None:
        on_start, on_done = _reaction_hooks(event.get("channel", ""), event.get("ts", ""))
        _dispatch(router.handle_app_mention(event, on_start=on_start, on_done=on_done), say)

    try:
        app.client.chat_postMessage(channel=owner_channel, text="Fig is online.")
        logger.info("Posted startup message to Slack")
    except Exception:
        logger.exception("Failed to post startup message to Slack")

    logger.info("Connecting to Slack via Socket Mode")
    SocketModeHandler(app, config.settings.slack_app_token).start()
