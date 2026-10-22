"""Turns long raw URLs in Slack replies into short clickable labels, using Slack's own
`<url|label>` syntax -- so there's no third-party shortener, private/LAN addresses work,
and the real link is untouched underneath. Code spans/blocks and links that already have
a label are left alone."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

MIN_URL_LENGTH = 45
MAX_LABEL_LENGTH = 40
TRAILING_PUNCTUATION = ".,;:!?)]}'\""
URL_PATTERN = re.compile(r"(?<![<|\w/=])(https?://[^\s<>|`]+)")
CODE_PATTERN = re.compile(r"(```.*?```|`[^`\n]*`)", re.DOTALL)


def label_for(url: str) -> str:
    parts = urlsplit(url)
    host = (parts.hostname or url).removeprefix("www.")
    segments = [s for s in parts.path.split("/") if s]
    if not segments:
        label = host
    elif len(segments) == 1:
        label = f"{host}/{segments[0]}"
    else:
        label = f"{host}/…/{segments[-1]}"
    return label if len(label) <= MAX_LABEL_LENGTH else label[: MAX_LABEL_LENGTH - 1] + "…"


def _replace(match: re.Match[str]) -> str:
    raw = match.group(1)
    url = raw.rstrip(TRAILING_PUNCTUATION)
    trailing = raw[len(url):]
    if len(url) < MIN_URL_LENGTH:
        return raw
    return f"<{url}|{label_for(url)}>{trailing}"


def shorten_links(text: str) -> str:
    pieces = CODE_PATTERN.split(text)
    # re.split with a capture group alternates plain text (even) and code (odd)
    return "".join(piece if i % 2 else URL_PATTERN.sub(_replace, piece) for i, piece in enumerate(pieces))
