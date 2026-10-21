"""Email summary interface for a later phase -- not implemented in Phase 1.

The intended shape: a `fetch_recent_emails(since) -> list[EmailSummary]` backed by
the Gmail API or IMAP, invoked each morning by a `figai.scheduler` job, whose
result is turned into one message posted to Slack. Treat fetched email content as
untrusted data, the same as every other tool's output -- never let it trigger a
side-effectful tool on its own authority.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass
class EmailSummary:
    sender: str
    subject: str
    snippet: str
    received_at: datetime


class EmailSource(Protocol):
    def fetch_recent(self, since: datetime) -> list[EmailSummary]: ...
