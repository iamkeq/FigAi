"""Scheduled checks: "is the miner at http://192.168.50.57/ up?" Fig probes each check
on its interval and speaks up only when the state changes -- down (after a couple of
consecutive failures, so one blip doesn't page you) and back up -- never on every run."""

from __future__ import annotations

import re
import socket
import time
from dataclasses import asdict, dataclass
from typing import Any, Callable

import requests

from figai.logging_setup import get_logger

logger = get_logger("monitoring")

PROBE_TIMEOUT_SECONDS = 10
MIN_INTERVAL_MINUTES = 1
FAILURES_BEFORE_ALERT = 2
MAX_CHECKS = 20
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _.-]{0,39}$")

Probe = Callable[["Check"], tuple[bool, str]]
Notify = Callable[[str, str], None]  # (channel, text)


class CheckError(Exception):
    pass


@dataclass
class Check:
    name: str
    kind: str  # "http" or "tcp"
    target: str  # a URL, or host:port
    interval_minutes: float
    channel: str
    last_run: float | None = None
    last_ok: bool | None = None
    last_detail: str = ""
    consecutive_failures: int = 0
    down_since: float | None = None
    alerted: bool = False


def default_probe(check: Check) -> tuple[bool, str]:
    started = time.monotonic()
    try:
        if check.kind == "http":
            response = requests.get(check.target, timeout=PROBE_TIMEOUT_SECONDS, allow_redirects=True)
            ms = (time.monotonic() - started) * 1000
            ok = response.status_code < 400
            return ok, f"HTTP {response.status_code} in {ms:.0f} ms"
        host, port = check.target.rsplit(":", 1)
        with socket.create_connection((host, int(port)), timeout=PROBE_TIMEOUT_SECONDS):
            pass
        return True, f"port open in {(time.monotonic() - started) * 1000:.0f} ms"
    except Exception as exc:  # noqa: BLE001 - every failure mode is just "down"
        return False, f"{type(exc).__name__}: {str(exc)[:80]}"


def _format_duration(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 60:
        return f"{minutes} min"
    hours, rest = divmod(minutes, 60)
    return f"{hours} h {rest} min" if rest else f"{hours} h"


class CheckManager:
    def __init__(
        self,
        notify: Notify,
        probe: Probe = default_probe,
        clock: Callable[[], float] = time.time,
        on_change: Callable[[], None] | None = None,
        failures_before_alert: int = FAILURES_BEFORE_ALERT,
    ) -> None:
        self._notify = notify
        self._probe = probe
        self._clock = clock
        self.on_change = on_change
        self._failures_before_alert = failures_before_alert
        self._checks: dict[str, Check] = {}

    # -- definitions ---------------------------------------------------------

    def add(self, name: str, target: str, interval_minutes: float, channel: str, kind: str | None = None) -> Check:
        name = name.strip()
        if not NAME_PATTERN.match(name):
            raise CheckError("Name must be 1-40 letters, digits, spaces, dots, dashes or underscores.")
        if name.lower() in {n.lower() for n in self._checks}:
            raise CheckError(f"A check named '{name}' already exists; remove it first to change it.")
        if len(self._checks) >= MAX_CHECKS:
            raise CheckError(f"At most {MAX_CHECKS} checks.")
        if interval_minutes < MIN_INTERVAL_MINUTES:
            raise CheckError(f"Check at most once a minute (got {interval_minutes}).")
        target = target.strip()
        kind = kind or ("http" if target.lower().startswith(("http://", "https://")) else "tcp")
        if kind == "http" and not target.lower().startswith(("http://", "https://")):
            raise CheckError("An http check needs a full URL like http://192.168.50.57/")
        if kind == "tcp" and not re.fullmatch(r"[^\s:]+:\d{1,5}", target):
            raise CheckError("A tcp check needs host:port, e.g. 192.168.50.57:80")
        if kind not in ("http", "tcp"):
            raise CheckError("kind must be 'http' or 'tcp'.")
        check = Check(name=name, kind=kind, target=target, interval_minutes=interval_minutes, channel=channel)
        self._checks[name] = check
        self._changed()
        return check

    def remove(self, name: str) -> bool:
        for existing in list(self._checks):
            if existing.lower() == name.strip().lower():
                del self._checks[existing]
                self._changed()
                return True
        return False

    def get(self, name: str) -> Check | None:
        return next((c for n, c in self._checks.items() if n.lower() == name.strip().lower()), None)

    def list(self) -> list[Check]:
        return sorted(self._checks.values(), key=lambda c: c.name.lower())

    def down(self) -> list[Check]:
        return [c for c in self.list() if c.alerted or (c.last_ok is False and c.consecutive_failures > 0)]

    # -- running -------------------------------------------------------------

    def tick(self) -> None:
        now = self._clock()
        for check in self.list():
            if check.last_run is None or now - check.last_run >= check.interval_minutes * 60:
                self._run(check)

    def run_now(self, name: str) -> Check:
        check = self.get(name)
        if check is None:
            raise CheckError(f"No check named '{name}'.")
        self._run(check)
        return check

    def _run(self, check: Check) -> None:
        ok, detail = self._probe(check)
        now = self._clock()
        check.last_run, check.last_ok, check.last_detail = now, ok, detail
        if ok:
            if check.alerted:
                downtime = _format_duration(now - (check.down_since or now))
                self._send(check, f":white_check_mark: *{check.name}* is back up after {downtime} ({detail}).")
            check.consecutive_failures, check.down_since, check.alerted = 0, None, False
        else:
            check.consecutive_failures += 1
            if check.down_since is None:
                check.down_since = now
            if check.consecutive_failures >= self._failures_before_alert and not check.alerted:
                check.alerted = True
                self._send(check, f":rotating_light: *{check.name}* is DOWN -- {check.target} ({detail}).")
        self._changed()

    def _send(self, check: Check, text: str) -> None:
        try:
            self._notify(check.channel, text)
        except Exception:  # noqa: BLE001 - a Slack hiccup must not break checking
            logger.exception("Could not deliver the alert for %s", check.name)

    # -- persistence ---------------------------------------------------------

    def to_state(self) -> list[dict[str, Any]]:
        return [asdict(c) for c in self.list()]

    def restore(self, state: list[dict[str, Any]]) -> None:
        for entry in state:
            try:
                check = Check(**entry)
            except TypeError:
                logger.warning("Skipping an unreadable saved check: %s", entry)
                continue
            self._checks[check.name] = check

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()

    def describe(self, check: Check) -> str:
        if check.last_ok is None:
            status = "not run yet"
        elif check.last_ok:
            status = f"up ({check.last_detail})"
        else:
            status = f"DOWN ({check.last_detail})"
        return f"{check.name}: {check.target} every {check.interval_minutes:g} min -- {status}"
