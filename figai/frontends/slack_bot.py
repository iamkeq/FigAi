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

logger = get_logger("slack.bot")


def build_file_sender(app: App, channel: str):
    def _send(path: Path) -> None:
        app.client.files_upload_v2(channel=channel, file=str(path), filename=path.name)

    return _send


def run_slack_bot(config: Config, on_ready: Callable[[SlackRouter], None] | None = None) -> None:
    app = App(token=config.settings.slack_bot_token)
    owner_channel = config.settings.allowed_slack_user_id

    session = AgentSession(config, file_sender=build_file_sender(app, owner_channel))
    router = SlackRouter(session=session, config=config, allowed_user_id=owner_channel)

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

    @app.event("message")
    def handle_message(event: dict[str, Any], say: Any) -> None:
        _dispatch(router.handle_event(event), say)

    @app.event("app_mention")
    def handle_app_mention(event: dict[str, Any], say: Any) -> None:
        _dispatch(router.handle_app_mention(event), say)

    try:
        app.client.chat_postMessage(channel=owner_channel, text="Fig is online.")
        logger.info("Posted startup message to Slack")
    except Exception:
        logger.exception("Failed to post startup message to Slack")

    logger.info("Connecting to Slack via Socket Mode")
    SocketModeHandler(app, config.settings.slack_app_token).start()
