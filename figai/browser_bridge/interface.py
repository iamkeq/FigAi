"""Browser tab bridge interface for a later phase -- not implemented in Phase 1.

The intended shape: a small Chrome extension posts the user's open tabs (title +
URL, never page content without a separate explicit fetch) to a local endpoint
Fig exposes only on localhost; a `list_open_tabs` tool then reads that bridge's
latest snapshot. Tab titles/URLs are untrusted data like any other tool output.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass
class BrowserTab:
    title: str
    url: str


class BrowserTabSource(Protocol):
    def list_open_tabs(self) -> list[BrowserTab]: ...
