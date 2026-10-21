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
from figai.tools.channels import resolve_channel_id_by_name
from figai.update.announcement import build_startup_announcement, decide_pending_announcement
from figai.update.github_info import find_merged_pr_for_commit, get_head_sha, get_remote_owner_repo

logger = get_logger("slack.bot")

PENDING_ANNOUNCEMENT_KEY = "pending_startup_announcement"
LAST_ANNOUNCED_SHA_KEY = "last_announced_sha"

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

    store = JsonStore(Path(config.app.state_path))
    state = store.load()
    channel_lister = build_channel_lister(app)

    def _on_updated(_old_sha: str, new_sha: str) -> None:
        """Called by the self-update tool right after a successful pull: works out
        which PR the new code came from and leaves a marker for the next startup
        to announce. A failed lookup just means no PR detail, never a failed update."""
        pr = None
        try:
            owner_repo = get_remote_owner_repo()
            if owner_repo:
                pr = find_merged_pr_for_commit(*owner_repo, new_sha, token=config.settings.github_token)
        except Exception:
            logger.exception("Could not look up the PR for the updated commit")
        current = store.load()
        current[PENDING_ANNOUNCEMENT_KEY] = {"sha": new_sha, "pr": pr}
        store.save(current)

    session = AgentSession(
        config,
        file_sender=build_file_sender(app, owner_channel),
        message_sender=build_message_sender(app),
        channel_lister=channel_lister,
        on_updated=_on_updated,
    )
    router = SlackRouter(session=session, config=config, allowed_user_id=owner_channel)

    router.restore_state(state.get("slack", {}))
    session.reminder_scheduler.restore(state.get("reminders", []))

    def _save_state() -> None:
        # Merge rather than overwrite, so a pending update announcement left by
        # _on_updated survives the router's own save at the end of the same turn.
        try:
            current = store.load()
            current["slack"] = router.to_state()
            current["reminders"] = session.reminder_scheduler.to_state()
            store.save(current)
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

    def _lookup_pr(sha: str):
        try:
            owner_repo = get_remote_owner_repo()
            return find_merged_pr_for_commit(*owner_repo, sha, token=config.settings.github_token) if owner_repo else None
        except Exception:
            logger.exception("Could not look up the PR for %s", sha)
            return None

    try:
        head_sha: str | None = get_head_sha()
    except Exception:
        head_sha = None

    pending = decide_pending_announcement(
        state.get(PENDING_ANNOUNCEMENT_KEY), head_sha, state.get(LAST_ANNOUNCED_SHA_KEY), _lookup_pr
    )
    announcement = build_startup_announcement(pending)

    announce_channel = owner_channel
    try:
        resolved = resolve_channel_id_by_name(channel_lister, config.app.startup_announcement_channel)
        if resolved:
            announce_channel = resolved
        else:
            logger.warning(
                "Channel '%s' not found; announcing in your DM instead", config.app.startup_announcement_channel
            )
    except Exception:
        logger.exception("Could not resolve the announcement channel; announcing in your DM instead")

    posted = False
    for target in dict.fromkeys([announce_channel, owner_channel]):
        try:
            app.client.chat_postMessage(channel=target, text=announcement)
            logger.info("Posted startup message to %s", target)
            posted = True
            break
        except Exception:
            logger.exception("Failed to post startup message to %s", target)

    if posted:
        current = store.load()
        current.pop(PENDING_ANNOUNCEMENT_KEY, None)
        if head_sha:
            current[LAST_ANNOUNCED_SHA_KEY] = head_sha
        store.save(current)

    logger.info("Connecting to Slack via Socket Mode")
    SocketModeHandler(app, config.settings.slack_app_token).start()
