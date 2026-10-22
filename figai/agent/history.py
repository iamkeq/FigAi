"""Keeps conversation history from growing without bound. Every message is re-sent to the
model on every turn, so a long-lived DM or voice session gets slower and costlier the longer
it runs; we cap its length and let idle conversations start fresh."""

from __future__ import annotations

from typing import Any

Message = dict[str, Any]


def trim_history(conversation: list[Message], max_messages: int) -> int:
    """Drops the oldest messages in place until at most `max_messages` remain, always starting
    on a user message (never mid-exchange). Returns how many were dropped; 0 = disabled/no-op."""
    if max_messages <= 0 or len(conversation) <= max_messages:
        return 0
    drop = len(conversation) - max_messages
    while drop < len(conversation) and conversation[drop].get("role") != "user":
        drop += 1
    del conversation[:drop]
    return drop


def is_idle(last_active: float | None, now: float, idle_minutes: float) -> bool:
    """True when a conversation has been quiet for longer than `idle_minutes` (0 = never expires)."""
    return bool(idle_minutes > 0 and last_active is not None and now - last_active > idle_minutes * 60)
