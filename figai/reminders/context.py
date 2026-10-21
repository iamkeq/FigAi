"""Tracks which conversation (Slack channel/thread, or just 'the CLI') the
in-flight message came from, so set_reminder can default to 'notify me here'
without the model needing to know raw Slack channel IDs.

A single shared instance per AgentSession is fine for a single-owner bot
handling one message at a time; it is not safe for genuinely concurrent
multi-user conversations, which this app was never designed for."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ConversationContext:
    current_channel: str | None = None
    current_thread_ts: str | None = None
