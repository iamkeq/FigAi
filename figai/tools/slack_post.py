"""post_slack_message / resolve_slack_post: Fig posts a message to a Slack channel (or your DM)
on request, e.g. "post in general that dinner is at 6". Works the same from Slack and from voice.
Confirm-gated by default, since it speaks in a shared channel; set `confirm_slack_posts: false`
in config.yaml to post straight away."""

from __future__ import annotations

import json
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry
from figai.logging_setup import get_logger
from figai.tools.channels import ChannelLister, resolve_channel_id_by_name
from figai.tools.confirm import ConfirmationStore

logger = get_logger("tools.slack_post")
ACTION_TYPE = "slack_post"
MAX_TEXT_CHARS = 3000
MessageSender = Callable[[str, str, "str | None"], None]


def register_slack_post_tool(
    registry: ToolRegistry,
    confirmations: ConfirmationStore,
    message_sender: MessageSender | None,
    channel_lister: ChannelLister | None,
    owner_channel: str,
    confirm: bool = True,
) -> None:
    def _resolve_channel(channel: str | None) -> tuple[str, str]:
        """(channel id, label shown to the user). Default is the owner's DM."""
        if not channel or channel.strip().lower() in ("me", "dm", "my dm"):
            return owner_channel, "your DM"
        channel = channel.strip()
        if channel[:1] in ("C", "G", "D", "U") and channel.isalnum() and channel.upper() == channel:
            return channel, channel  # already an ID
        if channel_lister is None:
            raise ValueError("Channel lookup isn't available, so I can't turn a name into a channel.")
        channel_id = resolve_channel_id_by_name(channel_lister, channel)
        if channel_id is None:
            raise ValueError(f"I can't find a channel called '{channel}' (is Fig invited to it?).")
        return channel_id, f"#{channel.lstrip('#')}"

    def _send(channel_id: str, text: str, thread_ts: str | None) -> None:
        assert message_sender is not None
        message_sender(channel_id, text, thread_ts)
        logger.info("Posted a message to %s (%d chars)", channel_id, len(text))

    def _post(arguments: dict[str, Any]) -> str:
        if message_sender is None:
            return json.dumps({"error": "Slack isn't connected, so I can't post messages."})
        text = str(arguments["text"]).strip()
        if not text:
            return json.dumps({"error": "There's no message text to post."})
        if len(text) > MAX_TEXT_CHARS:
            return json.dumps({"error": f"That message is too long ({len(text)} chars; max {MAX_TEXT_CHARS})."})
        try:
            channel_id, label = _resolve_channel(arguments.get("channel"))
        except ValueError as exc:
            return json.dumps({"error": str(exc)})
        thread_ts = arguments.get("thread_ts") or None

        if not confirm or channel_id == owner_channel:
            try:
                _send(channel_id, text, thread_ts)
            except Exception as exc:  # noqa: BLE001
                return json.dumps({"error": f"Slack refused the message: {exc}"})
            return json.dumps({"status": "posted", "to": label})

        preview = f"Post to {label}: {text}"
        token = confirmations.propose(
            ACTION_TYPE, {"channel_id": channel_id, "text": text, "thread_ts": thread_ts, "label": label}, preview
        )
        return json.dumps(
            {
                "status": "awaiting_confirmation",
                "token": token,
                "preview": preview,
                "note": "Wait for the user's explicit yes in a LATER message before calling resolve_slack_post.",
            }
        )

    registry.register(
        Tool(
            name="post_slack_message",
            description=(
                "Post a message to a Slack channel (by name like 'general' or by ID) or to the user's DM "
                "(omit channel). Use when the user asks you to post/say/announce something somewhere -- "
                "from Slack or by voice. Posting to a channel asks the user to confirm first; posting to "
                "their own DM does not. The text is what gets posted, so write it as the final message."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "channel": {"type": "string", "description": "Channel name or ID; omit for the user's DM."},
                    "thread_ts": {"type": "string"},
                },
                "required": ["text"],
            },
            handler=_post,
        )
    )

    def _resolve(arguments: dict[str, Any]) -> str:
        try:
            payload = confirmations.resolve(arguments["token"], ACTION_TYPE)
        except (KeyError, TimeoutError) as exc:
            return json.dumps({"error": str(exc)})
        try:
            _send(payload["channel_id"], payload["text"], payload.get("thread_ts"))
        except Exception as exc:  # noqa: BLE001
            return json.dumps({"error": f"Slack refused the message: {exc}"})
        return json.dumps({"status": "posted", "to": payload["label"]})

    registry.register(
        Tool(
            name="resolve_slack_post",
            description="Post the message the user confirmed in a later message (token from post_slack_message).",
            parameters={"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
            handler=_resolve,
        )
    )
