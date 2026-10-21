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


# -- persistence (to_state/restore/on_change) ---------------------------------


def test_on_change_fires_on_schedule_cancel_and_fire() -> None:
    fired: list[Reminder] = []
    changes: list[str] = []
    scheduler, timers = make_scheduler(fired)
    scheduler.on_change = lambda: changes.append("changed")

    reminder = scheduler.schedule(60, "x", channel="C1")
    assert changes == ["changed"]

    timers[0].callback()
    assert changes == ["changed", "changed"]

    scheduler.schedule(60, "y", channel="C1")
    scheduler.cancel(scheduler.list_pending()[0].id)
    assert changes[-1] == "changed"


def test_to_state_serializes_pending_reminders() -> None:
    fired: list[Reminder] = []
    clock = FakeClock(start=1000.0)
    scheduler, _ = make_scheduler(fired, clock)
    scheduler.schedule(60, "take out the trash", channel="C1", thread_ts="100.1")

    assert scheduler.to_state() == [
        {"id": 1, "message": "take out the trash", "channel": "C1", "thread_ts": "100.1", "due_at": 1060.0}
    ]


def test_restore_reschedules_a_future_reminder() -> None:
    fired: list[Reminder] = []
    clock = FakeClock(start=1000.0)
    scheduler, timers = make_scheduler(fired, clock)

    scheduler.restore([{"id": 5, "message": "later", "channel": "C1", "thread_ts": None, "due_at": 1060.0}])

    assert len(timers) == 1
    assert scheduler.list_pending()[0].id == 5
    timers[0].callback()
    assert [r.message for r in fired] == ["later"]


def test_restore_fires_an_overdue_reminder_almost_immediately() -> None:
    fired: list[Reminder] = []
    clock = FakeClock(start=1000.0)
    scheduler, timers = make_scheduler(fired, clock)

    scheduler.restore([{"id": 5, "message": "overdue", "channel": "C1", "thread_ts": None, "due_at": 500.0}])

    timers[0].callback()
    assert [r.message for r in fired] == ["overdue"]


def test_restore_continues_ids_after_the_highest_restored_id() -> None:
    fired: list[Reminder] = []
    scheduler, _ = make_scheduler(fired)

    scheduler.restore([{"id": 7, "message": "x", "channel": "C1", "thread_ts": None, "due_at": 2000.0}])
    new_reminder = scheduler.schedule(60, "y", channel="C1")

    assert new_reminder.id == 8


def test_restore_then_to_state_round_trips() -> None:
    fired: list[Reminder] = []
    clock = FakeClock(start=1000.0)
    scheduler, _ = make_scheduler(fired, clock)
    state = [{"id": 3, "message": "x", "channel": "C1", "thread_ts": "9.9", "due_at": 1500.0}]

    scheduler.restore(state)

    assert scheduler.to_state() == state
