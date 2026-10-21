"""Scheduler interface for a later phase -- not implemented in Phase 1.

The intended shape: a thin wrapper around APScheduler that lets a front-end or
skill register a recurring job (e.g. "every morning at 7am") whose callback
posts through whatever front-end-specific delivery mechanism is relevant
(e.g. Slack `chat_postMessage`). Phase 1 ships no jobs and starts no scheduler;
this module exists so later phases (the email summary) have a stable interface
to target without redesigning the agent core.
"""

from __future__ import annotations

from typing import Callable, Protocol


class Scheduler(Protocol):
    def schedule_daily(self, hour: int, minute: int, job: Callable[[], None]) -> str:
        """Registers `job` to run once a day at the given local time. Returns a job id."""
        ...

    def cancel(self, job_id: str) -> None: ...
