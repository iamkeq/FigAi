"""Spoken commands that are matched in plain code instead of being left to the chat model.

A small or free chat model often answers "type in general the word potato" with the word
"potato" in the reply, instead of calling the posting tool. For the handful of phrasings that
are unambiguous -- posting to a channel, DMing you -- the words are parsed here and the tool is
called directly, so they work the same whatever model is selected."""

from __future__ import annotations

import re
from dataclasses import dataclass

from figai.tools.slack_post import CHAT_ALIASES

VERBS = r"(?:post|type|say|write|send|put|share|announce|tell)"
FILLER = r"^(?:(?:hey|ok|okay|please|fig|can you|could you|i want you to|go ahead and)[,.\s]+)*"
LEAD_IN = r"(?:(?:that|saying|the word|the words|the message|the text|the number)\s+)?"
DM_PATTERN = re.compile(
    rf"{FILLER}(?:(?:dm|direct message|message|ping|text)\s+me|send me a (?:dm|direct message))"
    rf"[,.:;\-\s]+(?:(?:that|saying|the word|the words|the number)\s+)?(?P<message>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SlackPost:
    channel: str  # a channel name, an alias like "chat", or "me" for a DM
    message: str


def _clean(message: str) -> str:
    return message.strip().strip('"“”').rstrip(".").strip()


def match_slack_post(text: str, channel_names: list[str] | tuple[str, ...] = ()) -> SlackPost | None:
    """"type in general the word potato" -> SlackPost("general", "potato"); None if it isn't that."""
    text = text.strip()
    names = sorted({*CHAT_ALIASES, "general", *channel_names}, key=len, reverse=True)
    channel = "|".join(re.escape(n) for n in names)
    pattern = re.compile(
        rf"{FILLER}{VERBS}\s+(?:a\s+message\s+)?(?:in|to|on|into)\s+(?:the\s+)?(?P<channel>{channel})(?:\s+channel)?"
        rf"[,.:;\-\s]+{LEAD_IN}(?P<message>.+)$",
        re.IGNORECASE,
    )
    found = pattern.match(text)
    if found:
        message = _clean(found.group("message"))
        return SlackPost(found.group("channel").lower(), message) if message else None
    dm = DM_PATTERN.match(text)
    if dm:
        message = _clean(dm.group("message"))
        return SlackPost("me", message) if message else None
    return None


RESET_PHRASES = {
    "new session", "new conversation", "start over", "start a new session", "start a new conversation",
    "start a fresh conversation", "reset", "reset the conversation", "reset the session", "reset session",
    "reset conversation", "forget everything", "forget that", "clear the conversation", "clear the session",
    "fresh start", "start fresh",
}


def is_reset_phrase(text: str) -> bool:
    """"start over", "new session", ... -- forget the voice conversation so far."""
    words = re.findall(r"[a-z']+", text.lower())
    while words and words[0] in ("hey", "ok", "okay", "please", "fig", "can", "you", "could"):
        words.pop(0)
    return " ".join(words) in RESET_PHRASES
