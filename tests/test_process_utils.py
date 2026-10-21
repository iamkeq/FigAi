from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from figai import process_utils
from figai.process_utils import (
    acquire_single_instance,
    ensure_std_streams,
    install_excepthooks,
    quiet_run,
    release_single_instance,
)


def test_quiet_run_adds_no_window_flag_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: captured.update(kw))
    monkeypatch.setattr(sys, "platform", "win32")

    quiet_run(["git", "pull"])

    assert captured["creationflags"] == process_utils.CREATE_NO_WINDOW


def test_quiet_run_leaves_other_platforms_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict = {}
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: captured.update(kw))
    monkeypatch.setattr(sys, "platform", "linux")

    quiet_run(["git", "pull"])

    assert "creationflags" not in captured


def test_ensure_std_streams_replaces_missing_streams_with_a_log_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(sys, "stdout", None)
    monkeypatch.setattr(sys, "stderr", None)

    ensure_std_streams(tmp_path)
    print("hello from pythonw")
    sys.stdout.flush()

    assert "hello from pythonw" in (tmp_path / process_utils.CONSOLE_LOG_NAME).read_text()
    sys.stdout.close()


def test_ensure_std_streams_is_a_noop_with_a_console(tmp_path: Path) -> None:
    before = (sys.stdout, sys.stderr)
    ensure_std_streams(tmp_path)
    assert (sys.stdout, sys.stderr) == before
    assert not (tmp_path / process_utils.CONSOLE_LOG_NAME).exists()


def test_excepthook_logs_instead_of_printing(monkeypatch: pytest.MonkeyPatch) -> None:
    logged: list[str] = []
    monkeypatch.setattr(process_utils.logger, "critical", lambda msg, *a, **kw: logged.append(msg))
    monkeypatch.setattr(sys, "excepthook", sys.excepthook)
    install_excepthooks()

    try:
        raise RuntimeError("boom")
    except RuntimeError:
        sys.excepthook(*sys.exc_info())

    assert logged == ["Uncaught exception"]


def test_single_instance_blocks_a_second_holder_until_released() -> None:
    port = 47999
    try:
        assert acquire_single_instance(port, timeout=1) is True
        assert acquire_single_instance(port, timeout=0.3) is False
        release_single_instance()
        assert acquire_single_instance(port, timeout=1) is True
    finally:
        release_single_instance()
