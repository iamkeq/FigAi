"""Helpers for running Fig as a windowless background process (pythonw.exe /
Task Scheduler): safe std streams, logged crashes, no flashing console windows
for child processes, and a single-instance guard."""

from __future__ import annotations

import socket
import subprocess
import sys
import threading
import time
from pathlib import Path
from typing import Any, TextIO

from figai.logging_setup import LOG_DIR, get_logger

logger = get_logger("process")

CREATE_NO_WINDOW = 0x08000000
SINGLE_INSTANCE_PORT = 47653
CONSOLE_LOG_NAME = "figai-console.log"

_instance_lock: socket.socket | None = None


def quiet_run(*args: Any, **kwargs: Any) -> subprocess.CompletedProcess:
    """subprocess.run that never pops a console window on Windows -- under
    pythonw a child console program (git, pip) would otherwise flash one."""
    if sys.platform == "win32":
        kwargs.setdefault("creationflags", CREATE_NO_WINDOW)
    # Under pythonw there is no valid stdin handle to inherit, and handing a bad one
    # to the child fails with "[WinError 50] The request is not supported".
    if "input" not in kwargs and "stdin" not in kwargs:
        kwargs["stdin"] = subprocess.DEVNULL
    return subprocess.run(*args, **kwargs)


def ensure_std_streams(log_dir: Path = LOG_DIR) -> None:
    """pythonw.exe has no console, so sys.stdout/sys.stderr are None and anything
    that writes to them (print, tracebacks, tqdm progress bars) is lost or can
    crash. Point them at a log file instead so nothing is lost."""
    if sys.stdout is not None and sys.stderr is not None:
        return
    log_dir.mkdir(parents=True, exist_ok=True)
    stream: TextIO = open(log_dir / CONSOLE_LOG_NAME, "a", buffering=1, encoding="utf-8")  # noqa: SIM115
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def detach_console() -> bool:
    """Hide and detach the console this process owns (Windows only).

    Python 3.13's venv pythonw.exe is a launcher that starts the real interpreter
    as the *console* python.exe, which allocates its own console window even
    though the task asked for a windowless pythonw. That child is the only
    process attached to that console, so freeing it closes the window. Returns
    True if a console was detached (std streams are then dead and need
    replacing)."""
    if sys.platform != "win32":
        return False
    import ctypes

    hwnd = ctypes.windll.kernel32.GetConsoleWindow()  # type: ignore[attr-defined]
    if not hwnd:
        return False
    ctypes.windll.user32.ShowWindow(hwnd, 0)  # type: ignore[attr-defined]  # SW_HIDE (classic console host)
    ctypes.windll.kernel32.FreeConsole()  # type: ignore[attr-defined]
    return True


def install_excepthooks() -> None:
    """Record uncaught exceptions (main and worker threads) in the log, since
    with no console window nobody would ever see the traceback."""

    def _main_hook(exc_type: type[BaseException], exc: BaseException, tb: Any) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        logger.critical("Uncaught exception", exc_info=(exc_type, exc, tb))

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        logger.critical(
            "Uncaught exception in thread %s",
            args.thread.name if args.thread else "?",
            exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
        )

    sys.excepthook = _main_hook
    threading.excepthook = _thread_hook


def acquire_single_instance(port: int = SINGLE_INSTANCE_PORT, timeout: float = 10.0) -> bool:
    """Holds a loopback port for the life of the process; a second Fig can't get
    it. Retries for `timeout` seconds so a restart (the new process starts while
    the old one is still exiting) doesn't lose the race against itself."""
    global _instance_lock
    deadline = time.monotonic() + timeout
    while True:
        candidate = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            candidate.bind(("127.0.0.1", port))
            candidate.listen(1)
            _instance_lock = candidate
            return True
        except OSError:
            candidate.close()
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.25)


def release_single_instance() -> None:
    global _instance_lock
    if _instance_lock is not None:
        _instance_lock.close()
        _instance_lock = None
