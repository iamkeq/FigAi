from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from figai import lifecycle
from figai.agent.tools import ToolRegistry
from figai.browser_bridge.state import BridgeState
from figai.gmail.client import GmailError
from figai.health import HealthChecker
from figai.logs_reader import error_count_since, last_error, recent_lines
from figai.tools.diagnostics import register_diagnostics_tools

LOG = """2026-10-21 20:29:30 INFO     figai.tools: Executing tool memory_write args={}
2026-10-21 20:29:31 WARNING  figai.ssh.memory: Remote vault unreachable (x)
2026-10-21 20:29:33 ERROR    figai.tools: Tool memory_write raised an error
Traceback (most recent call last):
  File "x.py", line 1, in f
ValueError: bad path
2026-10-21 20:29:35 INFO     figai.tools: Tool memory_list result={"error": "nope"}
"""


@pytest.fixture
def log(tmp_path: Path) -> Path:
    path = tmp_path / "figai.log"
    path.write_text(LOG, encoding="utf-8")
    return path


def test_recent_errors_include_the_exception_line(log: Path) -> None:
    lines = recent_lines("errors", 5, log)
    assert len(lines) == 1 and "raised an error" in lines[0] and "ValueError: bad path" in lines[0]
    assert last_error(log) == lines[0]


def test_recent_lines_filters(log: Path) -> None:
    assert len(recent_lines("warnings", 10, log)) == 2
    assert len(recent_lines("tools", 10, log)) == 3
    assert len(recent_lines("all", 2, log)) == 2
    assert recent_lines("errors", 5, log.with_name("missing.log")) == []
    with pytest.raises(ValueError):
        recent_lines("bogus", 5, log)


def test_error_count_since(log: Path) -> None:
    now = datetime(2026, 10, 21, 21, 0, 0)
    assert error_count_since(24, log, now) == 1
    assert error_count_since(24, log, datetime(2026, 10, 25)) == 0


def test_diagnostics_tools(log: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("figai.tools.diagnostics.recent_lines", lambda kind, count: recent_lines(kind, count, log))
    registry = ToolRegistry()
    register_diagnostics_tools(registry, lambda: "all good")
    assert json.loads(registry.execute("health_check", "{}")) == {"report": "all good"}
    assert "ValueError" in json.dumps(json.loads(registry.execute("recent_logs", "{}")))
    assert "error" in json.loads(registry.execute("recent_logs", json.dumps({"kind": "bogus"})))


class FakeHttp:
    def __init__(self, status: int = 200, boom: bool = False) -> None:
        self.status, self.boom = status, boom

    def get(self, *_a, **_k):
        if self.boom:
            raise OSError("offline")
        return SimpleNamespace(status_code=self.status)


def make_checker(*, gmail: bool = False, bridge_token: bool = False, vault_ok: bool = True, http=None, errors: int = 0):
    config = SimpleNamespace(
        settings=SimpleNamespace(
            openrouter_api_key="k",
            gmail_address="a@b.c" if gmail else None,
            gmail_app_password="pw" if gmail else None,
            browser_bridge_token="t" * 16 if bridge_token else None,
        )
    )

    class Remote:
        def list_dir(self, _p: str = "") -> list[str]:
            if not vault_ok:
                raise OSError("down")
            return ["a/", "b.md"]

    return HealthChecker(
        config, lambda: Remote(), BridgeState(), http=http or FakeHttp(), get_sha=lambda: "abcdef123456",
        describe_models=lambda: "chat: m", error_counter=lambda _h: errors,
    )


def by_name(results):
    return {r.name: r for r in results}


def test_health_all_green_and_skips_unconfigured() -> None:
    results = by_name(make_checker().run())
    assert results["Fig"].ok and "abcdef1" in results["Fig"].detail
    assert results["OpenRouter"].ok and results["Vault"].ok and results["Recent errors"].ok
    assert results["Gmail"].ok is None and results["Chrome bridge"].ok is None


def test_health_reports_failures_without_crashing(monkeypatch: pytest.MonkeyPatch) -> None:
    checker = make_checker(vault_ok=False, http=FakeHttp(boom=True), errors=3, bridge_token=True)
    results = by_name(checker.run())
    assert results["Vault"].ok is False and "local vault" in results["Vault"].detail
    assert results["OpenRouter"].ok is False
    assert results["Recent errors"].ok is False and "3" in results["Recent errors"].detail
    assert results["Chrome bridge"].ok is False
    assert ":x:" in checker.report()


def test_health_gmail_failure_and_bad_key(monkeypatch: pytest.MonkeyPatch) -> None:
    from contextlib import contextmanager

    @contextmanager
    def failing(*_a, **_k):
        raise GmailError("Gmail IMAP error: bad login")
        yield

    monkeypatch.setattr("figai.health.open_mailbox", failing)
    results = by_name(make_checker(gmail=True, http=FakeHttp(status=401)).run())
    assert results["Gmail"].ok is False and "bad login" in results["Gmail"].detail
    assert results["OpenRouter"].ok is False and "401" in results["OpenRouter"].detail


def test_running_marker_detects_unclean_stop(tmp_path: Path) -> None:
    marker = tmp_path / "data" / "figai.running"
    assert lifecycle.mark_started(marker) is False
    assert marker.exists()
    assert lifecycle.mark_started(marker) is True  # never cleaned: previous run died
    lifecycle.mark_clean_stop()
    assert not marker.exists()
    assert lifecycle.mark_started(marker) is False
    lifecycle.mark_clean_stop()


def test_update_result_is_read_once_and_tolerates_bom(tmp_path: Path) -> None:
    path = tmp_path / "update_result.json"
    assert lifecycle.take_update_result(path) is None
    path.write_text(json.dumps({"status": "rolled_back"}), encoding="utf-8-sig")
    assert lifecycle.take_update_result(path) == {"status": "rolled_back"}
    assert not path.exists()
    path.write_text("{not json", encoding="utf-8")
    assert lifecycle.take_update_result(path)["status"] == "unknown"


def test_startup_alerts() -> None:
    assert lifecycle.build_startup_alerts(False, None, None) == []
    rolled = lifecycle.build_startup_alerts(
        False, {"status": "rolled_back", "time": "04:00", "attempted": "b" * 40, "previous": "a" * 40}, None
    )
    assert len(rolled) == 1 and "rolled back" in rolled[0] and "aaaaaaa" in rolled[0] and "bbbbbbb" in rolled[0]
    crash = lifecycle.build_startup_alerts(True, None, "2026 ERROR x: boom\n    ValueError")
    assert len(crash) == 1 and "unexpectedly" in crash[0] and "boom" in crash[0]
    both = lifecycle.build_startup_alerts(True, {"status": "pull_failed"}, None)
    assert len(both) == 2
    assert "rollback failed" in lifecycle.build_startup_alerts(False, {"status": "rollback_failed"}, None)[0]
