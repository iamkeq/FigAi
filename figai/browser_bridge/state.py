"""Thread-safe shared state between the bridge HTTP server (which the Chrome
extension talks to) and the agent tools: the latest tab snapshot, and a queue
of commands waiting for the extension to pick up."""

from __future__ import annotations

import threading
import time
from typing import Any, Callable

CONNECTED_MAX_AGE_SECONDS = 60


class BridgeState:
    def __init__(self, clock: Callable[[], float] = time.time) -> None:
        self._clock = clock
        self._cond = threading.Condition()
        self._tabs: list[dict[str, Any]] = []
        self._tabs_at: float | None = None
        self._last_seen: float | None = None
        self._commands: list[dict[str, Any]] = []

    def touch(self) -> None:
        with self._cond:
            self._last_seen = self._clock()

    def is_connected(self, max_age: float = CONNECTED_MAX_AGE_SECONDS) -> bool:
        with self._cond:
            return self._last_seen is not None and self._clock() - self._last_seen <= max_age

    def set_tabs(self, tabs: list[dict[str, Any]]) -> None:
        with self._cond:
            self._tabs = tabs
            self._tabs_at = self._clock()

    def get_tabs(self) -> tuple[list[dict[str, Any]], float | None]:
        """Returns (tabs, seconds since the snapshot), or ([], None) if none yet."""
        with self._cond:
            if self._tabs_at is None:
                return [], None
            return list(self._tabs), self._clock() - self._tabs_at

    def queue_command(self, command: dict[str, Any]) -> None:
        with self._cond:
            self._commands.append(command)
            self._cond.notify_all()

    def wait_for_commands(self, timeout: float) -> list[dict[str, Any]]:
        """Long-poll: returns queued commands immediately if any, else waits up
        to `timeout` seconds for one to arrive."""
        with self._cond:
            if not self._commands:
                self._cond.wait(timeout=timeout)
            commands, self._commands = self._commands, []
            return commands
