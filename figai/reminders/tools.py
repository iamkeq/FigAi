"""set_reminder / list_reminders / cancel_reminder: schedule a one-off message
to be delivered back to a channel (defaulting to wherever it was requested)
after a delay."""

from __future__ import annotations

import json
import time
from typing import Any

from figai.agent.tools import Tool, ToolRegistry
from figai.reminders.context import ConversationContext
from figai.reminders.scheduler import ReminderScheduler


def register_reminder_tools(registry: ToolRegistry, scheduler: ReminderScheduler, context: ConversationContext) -> None:
    def _set(arguments: dict[str, Any]) -> str:
        delay_minutes = arguments["delay_minutes"]
        message = arguments["message"]
        explicit_channel = arguments.get("channel")

        if delay_minutes <= 0:
            return json.dumps({"error": "delay_minutes must be positive"})

        channel = explicit_channel or context.current_channel
        if not channel:
            return json.dumps(
                {"error": "No channel to notify -- pass 'channel' explicitly (no Slack conversation is active)."}
            )

        # Only reply in the same thread when we're defaulting to "here" --
        # an explicitly named channel is a fresh top-level message there.
        thread_ts = None if explicit_channel else context.current_thread_ts

        reminder = scheduler.schedule(delay_minutes * 60, message, channel, thread_ts)
        return json.dumps({"status": "scheduled", "id": reminder.id, "channel": channel, "in_minutes": delay_minutes})

    registry.register(
        Tool(
            name="set_reminder",
            description=(
                "Schedule a one-off reminder message to be sent after a delay. Defaults to notifying the "
                "current conversation; pass 'channel' to send it somewhere else instead. Reminders are lost "
                "if Fig restarts before they fire."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "delay_minutes": {"type": "number", "description": "Minutes from now to send the reminder."},
                    "message": {"type": "string"},
                    "channel": {
                        "type": "string",
                        "description": "A specific Slack channel ID to notify instead of the current conversation.",
                    },
                },
                "required": ["delay_minutes", "message"],
            },
            handler=_set,
        )
    )

    def _list(_arguments: dict[str, Any]) -> str:
        now = time.time()
        return json.dumps(
            [
                {
                    "id": r.id,
                    "message": r.message,
                    "channel": r.channel,
                    "minutes_remaining": round((r.due_at - now) / 60, 1),
                }
                for r in scheduler.list_pending()
            ]
        )

    registry.register(
        Tool(
            name="list_reminders",
            description="List pending reminders and how many minutes remain until each fires.",
            parameters={"type": "object", "properties": {}},
            handler=_list,
        )
    )

    def _cancel(arguments: dict[str, Any]) -> str:
        cancelled = scheduler.cancel(arguments["id"])
        return json.dumps({"status": "cancelled" if cancelled else "not_found"})

    registry.register(
        Tool(
            name="cancel_reminder",
            description="Cancel a pending reminder by its id, as shown by list_reminders.",
            parameters={"type": "object", "properties": {"id": {"type": "integer"}}, "required": ["id"]},
            handler=_cancel,
        )
    )
