from __future__ import annotations

import json
import subprocess

from figai.agent.tools import ToolRegistry
from figai.tools.confirm import ConfirmationStore
from figai.tools.self_update import register_self_update_tools


class FakeTimer:
    def __init__(self, callback) -> None:
        self.callback = callback


def make_registry(runner=None, restart=None):
    timers: list[FakeTimer] = []

    def timer_factory(delay_seconds, callback):
        timer = FakeTimer(callback)
        timers.append(timer)
        return timer

    restarted: list[str] = []
    calls: list[list[str]] = []

    def default_runner(cmd, **kwargs):
        calls.append(cmd)

    registry = ToolRegistry()
    register_self_update_tools(
        registry,
        ConfirmationStore(),
        runner=runner or default_runner,
        restart=restart or (lambda: restarted.append("restarted")),
        timer_factory=timer_factory,
    )
    return registry, calls, restarted, timers


def test_propose_returns_awaiting_confirmation() -> None:
    registry, _, _, _ = make_registry()

    result = json.loads(registry.execute("propose_update_fig", "{}"))

    assert result["status"] == "awaiting_confirmation"
    assert "token" in result


def test_resolve_without_prior_proposal_returns_error() -> None:
    registry, _, _, _ = make_registry()

    result = json.loads(registry.execute("resolve_update_fig", json.dumps({"token": "madeup"})))

    assert "error" in result


def test_resolve_pulls_and_reinstalls_then_schedules_restart() -> None:
    registry, calls, restarted, timers = make_registry()

    propose = json.loads(registry.execute("propose_update_fig", "{}"))
    result = json.loads(registry.execute("resolve_update_fig", json.dumps({"token": propose["token"]})))

    assert result["status"] == "updated"
    assert calls[0][:2] == ["git", "pull"]
    assert "pip" in calls[1] or "install" in calls[1]
    assert len(timers) == 1
    assert restarted == []  # not restarted until the timer actually fires

    timers[0].callback()
    assert restarted == ["restarted"]


def test_resolve_does_not_restart_if_git_pull_fails() -> None:
    def failing_runner(cmd, **kwargs):
        raise subprocess.CalledProcessError(1, cmd, stderr="merge conflict")

    registry, _, restarted, timers = make_registry(runner=failing_runner)
    propose = json.loads(registry.execute("propose_update_fig", "{}"))

    result = json.loads(registry.execute("resolve_update_fig", json.dumps({"token": propose["token"]})))

    assert "error" in result
    assert "merge conflict" in result["error"]
    assert timers == []
    assert restarted == []


def test_resolve_cannot_reuse_the_same_token_twice() -> None:
    registry, _, _, _ = make_registry()
    propose = json.loads(registry.execute("propose_update_fig", "{}"))
    registry.execute("resolve_update_fig", json.dumps({"token": propose["token"]}))

    second_result = json.loads(registry.execute("resolve_update_fig", json.dumps({"token": propose["token"]})))

    assert "error" in second_result
