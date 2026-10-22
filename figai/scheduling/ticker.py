"""A tiny background loop that calls `tick` every few seconds. The briefing and the
scheduled checks decide for themselves whether anything is due, so one ticker drives
both and a missed beat (PC asleep) just means they catch up on the next tick."""

from __future__ import annotations

import threading
from typing import Callable

from figai.logging_setup import get_logger

logger = get_logger("scheduling")

DEFAULT_INTERVAL_SECONDS = 30


class Ticker:
    def __init__(self, tick: Callable[[], None], interval_seconds: float = DEFAULT_INTERVAL_SECONDS) -> None:
        self._tick = tick
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._run, name="fig-ticker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self._tick()
            except Exception:  # noqa: BLE001 - one bad tick must never kill the loop
                logger.exception("Scheduled tick failed")
