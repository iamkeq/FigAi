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


def _fake_windll(hwnd: int):
    import types

    calls: list[tuple] = []
    kernel32 = types.SimpleNamespace(GetConsoleWindow=lambda: hwnd, FreeConsole=lambda: calls.append(("free",)))
    user32 = types.SimpleNamespace(ShowWindow=lambda h, cmd: calls.append(("show", h, cmd)))
    return types.SimpleNamespace(kernel32=kernel32, user32=user32), calls


def test_detach_console_hides_then_frees_an_owned_console(monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes

    windll, calls = _fake_windll(hwnd=1234)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)

    assert process_utils.detach_console() is True
    assert calls == [("show", 1234, 0), ("free",)]


def test_detach_console_does_nothing_without_a_console(monkeypatch: pytest.MonkeyPatch) -> None:
    import ctypes

    windll, calls = _fake_windll(hwnd=0)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "windll", windll, raising=False)

    assert process_utils.detach_console() is False
    assert calls == []


def test_detach_console_is_a_noop_off_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sys, "platform", "linux")
    assert process_utils.detach_console() is False


def test_logging_skips_console_handler_when_stdout_is_not_a_terminal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import io
    import logging

    from figai import logging_setup

    monkeypatch.setattr(logging_setup, "LOG_DIR", tmp_path)
    monkeypatch.setattr(logging_setup, "LOG_FILE", tmp_path / "figai.log")
    monkeypatch.setattr(sys, "stdout", io.StringIO())  # not a tty, like the log file after detaching

    logger = logging_setup.setup_logging("INFO")

    assert not any(type(h) is logging.StreamHandler for h in logger.handlers)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


def test_quiet_run_gives_children_no_stdin(monkeypatch) -> None:
    import subprocess

    from figai import process_utils

    seen: dict = {}
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: seen.update(k))
    process_utils.quiet_run(["git", "pull"])
    assert seen["stdin"] == subprocess.DEVNULL
    seen.clear()
    process_utils.quiet_run(["x"], input="data")
    assert "stdin" not in seen
