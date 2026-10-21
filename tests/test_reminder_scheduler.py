from __future__ import annotations

from figai.reminders.scheduler import Reminder, ReminderScheduler


class FakeTimer:
    def __init__(self, callback) -> None:
        self.callback = callback
        self.cancelled = False

    def cancel(self) -> None:
        self.cancelled = True


class FakeClock:
    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now


def make_scheduler(fired: list[Reminder], clock: FakeClock | None = None) -> tuple[ReminderScheduler, list[FakeTimer]]:
    timers: list[FakeTimer] = []

    def timer_factory(delay_seconds, callback):
        timer = FakeTimer(callback)
        timers.append(timer)
        return timer

    scheduler = ReminderScheduler(on_fire=fired.append, timer_factory=timer_factory, clock=clock or FakeClock())
    return scheduler, timers


def test_schedule_does_not_fire_until_the_timer_callback_runs() -> None:
    fired: list[Reminder] = []
    scheduler, timers = make_scheduler(fired)

    scheduler.schedule(60, "take out the trash", channel="C1")

    assert fired == []
    assert len(timers) == 1


def test_timer_firing_calls_on_fire_with_the_reminder() -> None:
    fired: list[Reminder] = []
    scheduler, timers = make_scheduler(fired)

    reminder = scheduler.schedule(60, "take out the trash", channel="C1")
    timers[0].callback()

    assert fired == [reminder]


def test_list_pending_sorts_by_due_time() -> None:
    fired: list[Reminder] = []
    clock = FakeClock()
    scheduler, _ = make_scheduler(fired, clock)

    later = scheduler.schedule(600, "later", channel="C1")
    sooner = scheduler.schedule(60, "sooner", channel="C1")

    assert scheduler.list_pending() == [sooner, later]


def test_cancel_removes_pending_reminder_and_cancels_its_timer() -> None:
    fired: list[Reminder] = []
    scheduler, timers = make_scheduler(fired)
    reminder = scheduler.schedule(60, "take out the trash", channel="C1")

    cancelled = scheduler.cancel(reminder.id)

    assert cancelled is True
    assert timers[0].cancelled is True
    assert scheduler.list_pending() == []


def test_cancel_unknown_id_returns_false() -> None:
    fired: list[Reminder] = []
    scheduler, _ = make_scheduler(fired)

    assert scheduler.cancel(999) is False


def test_cancelled_reminder_does_not_fire_even_if_timer_callback_runs_anyway() -> None:
    fired: list[Reminder] = []
    scheduler, timers = make_scheduler(fired)
    reminder = scheduler.schedule(60, "take out the trash", channel="C1")

    scheduler.cancel(reminder.id)
    timers[0].callback()  # simulates a race where the timer already fired

    assert fired == []


def test_schedule_stores_channel_and_thread_ts() -> None:
    fired: list[Reminder] = []
    scheduler, _ = make_scheduler(fired)

    reminder = scheduler.schedule(60, "ping me", channel="C1", thread_ts="100.1")

    assert reminder.channel == "C1"
    assert reminder.thread_ts == "100.1"
