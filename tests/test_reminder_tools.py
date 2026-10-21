from __future__ import annotations

import json

from figai.agent.tools import ToolRegistry
from figai.reminders.context import ConversationContext
from figai.reminders.scheduler import ReminderScheduler
from figai.reminders.tools import register_reminder_tools


def make_registry(context: ConversationContext | None = None) -> tuple[ToolRegistry, ReminderScheduler]:
    scheduler = ReminderScheduler(on_fire=lambda r: None)
    registry = ToolRegistry()
    register_reminder_tools(registry, scheduler, context or ConversationContext())
    return registry, scheduler


def test_set_reminder_defaults_to_current_conversation_channel() -> None:
    context = ConversationContext(current_channel="D1", current_thread_ts=None)
    registry, scheduler = make_registry(context)

    result = json.loads(
        registry.execute("set_reminder", json.dumps({"delay_minutes": 5, "message": "check the oven"}))
    )

    assert result["status"] == "scheduled"
    assert result["channel"] == "D1"
    pending = scheduler.list_pending()
    assert len(pending) == 1
    assert pending[0].channel == "D1"
    assert pending[0].message == "check the oven"


def test_set_reminder_uses_explicit_channel_over_context() -> None:
    context = ConversationContext(current_channel="D1", current_thread_ts="100.1")
    registry, scheduler = make_registry(context)

    registry.execute(
        "set_reminder", json.dumps({"delay_minutes": 5, "message": "ping", "channel": "C999"})
    )

    reminder = scheduler.list_pending()[0]
    assert reminder.channel == "C999"
    assert reminder.thread_ts is None  # explicit channel means a fresh message, not the old thread


def test_set_reminder_defaults_keep_the_current_thread() -> None:
    context = ConversationContext(current_channel="C1", current_thread_ts="100.1")
    registry, scheduler = make_registry(context)

    registry.execute("set_reminder", json.dumps({"delay_minutes": 5, "message": "ping"}))

    assert scheduler.list_pending()[0].thread_ts == "100.1"


def test_set_reminder_without_any_channel_returns_error() -> None:
    registry, scheduler = make_registry(ConversationContext())

    result = json.loads(
        registry.execute("set_reminder", json.dumps({"delay_minutes": 5, "message": "ping"}))
    )

    assert "error" in result
    assert scheduler.list_pending() == []


def test_set_reminder_rejects_non_positive_delay() -> None:
    registry, scheduler = make_registry(ConversationContext(current_channel="D1"))

    result = json.loads(
        registry.execute("set_reminder", json.dumps({"delay_minutes": 0, "message": "ping"}))
    )

    assert "error" in result
    assert scheduler.list_pending() == []


def test_list_reminders_reports_pending_entries() -> None:
    registry, scheduler = make_registry(ConversationContext(current_channel="D1"))
    registry.execute("set_reminder", json.dumps({"delay_minutes": 10, "message": "first"}))
    registry.execute("set_reminder", json.dumps({"delay_minutes": 20, "message": "second"}))

    result = json.loads(registry.execute("list_reminders", "{}"))

    assert [r["message"] for r in result] == ["first", "second"]


def test_cancel_reminder_removes_it() -> None:
    registry, scheduler = make_registry(ConversationContext(current_channel="D1"))
    propose = json.loads(registry.execute("set_reminder", json.dumps({"delay_minutes": 10, "message": "first"})))

    result = json.loads(registry.execute("cancel_reminder", json.dumps({"id": propose["id"]})))

    assert result == {"status": "cancelled"}
    assert scheduler.list_pending() == []


def test_cancel_unknown_reminder_returns_not_found() -> None:
    registry, _ = make_registry(ConversationContext(current_channel="D1"))

    result = json.loads(registry.execute("cancel_reminder", json.dumps({"id": 999})))

    assert result == {"status": "not_found"}
