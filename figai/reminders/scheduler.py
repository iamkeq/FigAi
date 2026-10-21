"""One-off reminder scheduler. Pure/injectable timer so firing can be tested
deterministically without any real wall-clock waiting. State (pending
reminders) can be serialized via to_state()/restore() so a front-end can
persist it to disk and survive a restart -- the scheduler itself holds
nothing more durable than process memory."""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

MIN_RESTORE_DELAY_SECONDS = 0.1  # an overdue reminder fires almost immediately rather than being lost


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
    on_change: Callable[[], None] | None = None
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
        self._notify_change()
        return reminder

    def _fire(self, reminder_id: int) -> None:
        reminder = self._reminders.pop(reminder_id, None)
        self._timers.pop(reminder_id, None)
        if reminder is not None:
            self._notify_change()
            self.on_fire(reminder)

    def cancel(self, reminder_id: int) -> bool:
        if reminder_id not in self._reminders:
            return False
        del self._reminders[reminder_id]
        timer = self._timers.pop(reminder_id, None)
        if timer is not None:
            timer.cancel()
        self._notify_change()
        return True

    def list_pending(self) -> list[Reminder]:
        return sorted(self._reminders.values(), key=lambda r: r.due_at)

    def to_state(self) -> list[dict[str, Any]]:
        return [
            {"id": r.id, "message": r.message, "channel": r.channel, "thread_ts": r.thread_ts, "due_at": r.due_at}
            for r in self.list_pending()
        ]

    def restore(self, state: list[dict[str, Any]]) -> None:
        """Reschedules reminders from persisted state. A reminder that was
        already overdue while Fig was down fires almost immediately rather
        than being silently dropped."""
        max_id = 0
        for entry in state:
            reminder = Reminder(
                id=entry["id"],
                message=entry["message"],
                channel=entry["channel"],
                thread_ts=entry.get("thread_ts"),
                due_at=entry["due_at"],
            )
            max_id = max(max_id, reminder.id)
            delay = max(reminder.due_at - self.clock(), MIN_RESTORE_DELAY_SECONDS)
            self._reminders[reminder.id] = reminder
            self._timers[reminder.id] = self.timer_factory(delay, lambda rid=reminder.id: self._fire(rid))
        self._next_id = max(self._next_id, max_id + 1)

    def _notify_change(self) -> None:
        if self.on_change:
            self.on_change()
