"""In-memory one-off reminder scheduler. Pure/injectable timer so firing can be
tested deterministically without any real wall-clock waiting. Reminders are
lost if Fig restarts before they fire -- there is no persistence yet."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol


class Cancelable(Protocol):
    def cancel(self) -> None: ...


TimerFactory = Callable[[float, Callable[[], None]], Cancelable]


def default_timer_factory(delay_seconds: float, callback: Callable[[], None]) -> threading.Timer:
    timer = threading.Timer(delay_seconds, callback)
    timer.daemon = True
    timer.start()
    return timer


@dataclass
class Reminder:
    id: int
    message: str
    channel: str
    thread_ts: str | None
    due_at: float  # epoch seconds


@dataclass
class ReminderScheduler:
    on_fire: Callable[[Reminder], None]
    timer_factory: TimerFactory = default_timer_factory
    clock: Callable[[], float] = time.time
    _next_id: int = field(default=1, init=False)
    _reminders: dict[int, Reminder] = field(default_factory=dict, init=False)
    _timers: dict[int, Cancelable] = field(default_factory=dict, init=False)

    def schedule(self, delay_seconds: float, message: str, channel: str, thread_ts: str | None = None) -> Reminder:
        reminder_id = self._next_id
        self._next_id += 1
        reminder = Reminder(
            id=reminder_id, message=message, channel=channel, thread_ts=thread_ts, due_at=self.clock() + delay_seconds
        )
        self._reminders[reminder_id] = reminder
        self._timers[reminder_id] = self.timer_factory(delay_seconds, lambda: self._fire(reminder_id))
        return reminder

    def _fire(self, reminder_id: int) -> None:
        reminder = self._reminders.pop(reminder_id, None)
        self._timers.pop(reminder_id, None)
        if reminder is not None:
            self.on_fire(reminder)

    def cancel(self, reminder_id: int) -> bool:
        if reminder_id not in self._reminders:
            return False
        del self._reminders[reminder_id]
        timer = self._timers.pop(reminder_id, None)
        if timer is not None:
            timer.cancel()
        return True

    def list_pending(self) -> list[Reminder]:
        return sorted(self._reminders.values(), key=lambda r: r.due_at)
