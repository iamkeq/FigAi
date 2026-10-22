from __future__ import annotations

import json
from datetime import datetime
from types import SimpleNamespace

import pytest

from figai.agent.tools import ToolRegistry
from figai.briefing.briefing import BriefingManager, BriefingSources, build_briefing, parse_time
from figai.briefing.tools import register_briefing_tools
from figai.config import BriefingConfig
from figai.monitoring.checks import CheckError, CheckManager
from figai.monitoring.tools import register_check_tools
from figai.reminders.context import ConversationContext
from figai.scheduling.ticker import Ticker


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def make_manager(results: list[bool]):
    sent: list[tuple[str, str]] = []
    clock = Clock()
    outcomes = list(results)
    manager = CheckManager(
        notify=lambda ch, text: sent.append((ch, text)),
        probe=lambda _c: (outcomes.pop(0), "HTTP 200" if outcomes and False else "detail"),
        clock=clock,
    )
    return manager, sent, clock


# -- checks -------------------------------------------------------------------


def test_alerts_only_after_two_failures_and_once() -> None:
    manager, sent, clock = make_manager([False, False, False, False])
    manager.add("miner", "http://192.168.50.57/", 5, "C1")
    manager.tick()  # failure 1: no alert yet
    assert sent == []
    clock.now += 300
    manager.tick()  # failure 2: alert
    assert len(sent) == 1 and "DOWN" in sent[0][1] and sent[0][0] == "C1"
    clock.now += 300
    manager.tick()
    clock.now += 300
    manager.tick()  # still down: no repeat
    assert len(sent) == 1
    assert [c.name for c in manager.down()] == ["miner"]


def test_recovery_alert_reports_downtime() -> None:
    manager, sent, clock = make_manager([False, False, True])
    manager.add("miner", "http://x/", 1, "C1")
    for _ in range(3):
        manager.tick()
        clock.now += 600
    assert "DOWN" in sent[0][1]
    assert "back up after 10 min" in sent[1][1] or "back up after 20 min" in sent[1][1]
    assert manager.down() == []


def test_blip_does_not_alert() -> None:
    manager, sent, clock = make_manager([False, True, False, True])
    manager.add("m", "http://x/", 1, "C1")
    for _ in range(4):
        manager.tick()
        clock.now += 120
    assert sent == []


def test_not_run_before_interval_elapses() -> None:
    manager, _sent, clock = make_manager([True, True])
    manager.add("m", "http://x/", 10, "C1")
    manager.tick()
    clock.now += 60
    manager.tick()
    assert manager.get("m").last_run == 1000.0  # second tick did nothing


@pytest.mark.parametrize(
    "name,target,interval",
    [("", "http://x/", 5), ("ok", "ftp://x", 5), ("ok", "no-port", 5), ("ok", "http://x/", 0.5), ("bad/name", "http://x/", 5)],
)
def test_add_validation(name, target, interval) -> None:
    manager, _, _ = make_manager([])
    with pytest.raises(CheckError):
        manager.add(name, target, interval, "C1")


def test_duplicate_and_remove_and_kind_detection() -> None:
    manager, _, _ = make_manager([])
    assert manager.add("a", "192.168.1.5:80", 5, "C1").kind == "tcp"
    assert manager.add("b", "https://x/", 5, "C1").kind == "http"
    with pytest.raises(CheckError):
        manager.add("A", "http://x/", 5, "C1")
    assert manager.remove("a") and not manager.remove("a")


def test_state_round_trip_keeps_down_status() -> None:
    manager, _, clock = make_manager([False, False])
    manager.add("m", "http://x/", 1, "C1")
    manager.tick()
    clock.now += 60
    manager.tick()
    restored, sent, _ = make_manager([])
    restored.restore(manager.to_state())
    assert restored.get("m").alerted is True and restored.get("m").consecutive_failures == 2
    restored.restore([{"bogus": 1}])  # unreadable entries are skipped, not fatal


def test_notify_failure_does_not_break_checking() -> None:
    manager = CheckManager(notify=lambda *_a: 1 / 0, probe=lambda _c: (False, "x"), clock=Clock(), failures_before_alert=1)
    manager.add("m", "http://x/", 1, "C1")
    manager.tick()
    assert manager.get("m").alerted


def test_check_tools() -> None:
    manager, _, _ = make_manager([True, True])
    registry = ToolRegistry()
    context = ConversationContext()
    context.current_channel = "C_HERE"
    register_check_tools(registry, manager, context, "U_OWNER")

    def call(tool, **args):
        return json.loads(registry.execute(tool, json.dumps(args)))

    added = call("add_check", name="FigAxe2", target="http://192.168.50.57/")
    assert added["status"] == "added" and added["alerts_go_to"] == "C_HERE"
    assert "FigAxe2" in call("list_checks")["checks"][0]
    assert "up" in call("run_check", name="figaxe2")["result"]
    assert "error" in call("add_check", name="x", target="nonsense")
    assert call("remove_check", name="FigAxe2")["status"] == "removed"
    assert "error" in call("remove_check", name="FigAxe2")
    context.current_channel = None
    manager2, _, _ = make_manager([True])
    registry2 = ToolRegistry()
    register_check_tools(registry2, manager2, context, "U_OWNER")
    out = json.loads(registry2.execute("add_check", json.dumps({"name": "n", "target": "http://x/"})))
    assert out["alerts_go_to"] == "U_OWNER"


# -- briefing -----------------------------------------------------------------

NOW = datetime(2026, 10, 22, 7, 30)


def sources(email=None, hits=None, reminders=(), down=(), problems=()):
    def run_tool(name, args):
        if name == "summarize_emails":
            return email if email is not None else {"summary": "Nothing urgent."}
        return {"results": hits or []}

    return BriefingSources(run_tool, lambda: list(reminders), lambda: list(down), lambda: list(problems))


def test_briefing_has_every_section() -> None:
    reminder = SimpleNamespace(message="call the vet", due_at=datetime(2026, 10, 22, 15, 0).timestamp())
    tomorrow = SimpleNamespace(message="not today", due_at=datetime(2026, 10, 23, 9, 0).timestamp())
    text = build_briefing(
        sources(
            email={"summary": "Invoice due Friday."},
            hits=[{"path": "Crypto/plan.md", "snippet": "2026-10-22 check miners"}],
            reminders=[reminder, tomorrow],
            down=["FigAxe2: DOWN"],
            problems=["Vault: down"],
        ),
        BriefingConfig(),
        NOW,
    )
    assert "Thursday, October 22" in text
    assert "15:00 call the vet" in text and "not today" not in text
    assert "Invoice due Friday." in text and "Crypto/plan.md" in text
    assert "FigAxe2: DOWN" in text and "Vault: down" in text


def test_briefing_email_off_makes_no_email_call() -> None:
    calls: list[str] = []
    src = BriefingSources(lambda n, a: calls.append(n) or {}, lambda: [], lambda: [], lambda: [])
    text = build_briefing(src, BriefingConfig(include_email=False), NOW)
    assert "summarize_emails" not in calls and "Email" not in text
    assert "Nothing scheduled" in text


def test_briefing_email_error_is_shown_not_fatal() -> None:
    text = build_briefing(sources(email={"error": "No email model is set"}), BriefingConfig(), NOW)
    assert "skipped: No email model is set" in text


def make_briefing(now: datetime, enabled: bool = True):
    sent: list[tuple[str, str]] = []
    clock = {"now": now}
    manager = BriefingManager(
        BriefingConfig(enabled=enabled, time="07:30"),
        "U_OWNER",
        build=lambda s, n: "BRIEFING",
        notify=lambda ch, text: sent.append((ch, text)),
        now=lambda: clock["now"],
    )
    return manager, sent, clock


def test_briefing_sends_once_per_day_at_its_time() -> None:
    manager, sent, clock = make_briefing(datetime(2026, 10, 22, 7, 29))
    manager.tick()
    assert sent == []
    clock["now"] = datetime(2026, 10, 22, 7, 30, 20)
    manager.tick()
    manager.tick()
    assert sent == [("U_OWNER", "BRIEFING")]
    clock["now"] = datetime(2026, 10, 23, 7, 31)
    manager.tick()
    assert len(sent) == 2


def test_briefing_catches_up_after_downtime_but_not_at_night() -> None:
    manager, sent, clock = make_briefing(datetime(2026, 10, 22, 9, 0))
    manager.tick()
    assert len(sent) == 1
    manager2, sent2, _ = make_briefing(datetime(2026, 10, 22, 15, 0))
    manager2.tick()
    assert sent2 == []


def test_briefing_off_sends_nothing_but_now_still_works() -> None:
    manager, sent, _ = make_briefing(datetime(2026, 10, 22, 8, 0), enabled=False)
    manager.tick()
    assert sent == []
    manager.send_now()
    assert len(sent) == 1
    assert "off" in manager.status()


def test_briefing_configure_and_persistence() -> None:
    manager, _, _ = make_briefing(NOW, enabled=False)
    changes: list[int] = []
    manager.on_change = lambda: changes.append(1)
    manager.configure(enabled=True, time="6:05", include_email=False, channel="C9")
    assert manager.settings.time == "06:05" and manager.settings.channel == "C9" and changes
    state = manager.to_state()
    fresh, _, _ = make_briefing(NOW, enabled=False)
    fresh.restore(state)
    assert fresh.settings.enabled and fresh.settings.time == "06:05" and not fresh.settings.include_email
    fresh.restore({"time": "99:99"})  # unreadable settings are ignored
    assert fresh.settings.time == "06:05"
    with pytest.raises(ValueError):
        manager.configure(time="25:00")
    assert parse_time("7:30") == "07:30"


def test_briefing_tools() -> None:
    manager, sent, _ = make_briefing(NOW, enabled=False)
    registry = ToolRegistry()
    register_briefing_tools(registry, manager)
    out = json.loads(registry.execute("set_briefing", json.dumps({"enabled": True, "time": "08:15"})))
    assert "on" in out["status"] and "08:15" in out["status"]
    assert "error" in json.loads(registry.execute("set_briefing", json.dumps({"time": "nope"})))
    assert json.loads(registry.execute("send_briefing_now", "{}")) == {"status": "sent"}
    assert "on" in json.loads(registry.execute("briefing_status", "{}"))["status"]


def test_briefing_defaults_to_off() -> None:
    assert BriefingConfig().enabled is False


# -- ticker -------------------------------------------------------------------


def test_ticker_survives_a_failing_tick() -> None:
    import threading

    calls = []
    done = threading.Event()

    def tick():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("boom")
        done.set()

    ticker = Ticker(tick, interval_seconds=0.01)
    ticker.start()
    assert done.wait(2)
    ticker.stop()
