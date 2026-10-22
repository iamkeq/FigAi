"""One-shot health report: is everything Fig depends on actually working? Shared by the
`!health` command and the health_check tool. Each check is isolated and time-boxed, so one
broken dependency shows up as a red line instead of taking the report down."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Callable

import requests

from figai.browser_bridge.state import BridgeState
from figai.config import Config
from figai.gmail.client import GmailError, default_imap_factory, open_mailbox
from figai.logs_reader import error_count_since
from figai.update.github_info import get_head_sha

OPENROUTER_KEY_URL = "https://openrouter.ai/api/v1/auth/key"
HTTP_TIMEOUT_SECONDS = 8


@dataclass
class CheckResult:
    name: str
    ok: bool | None  # None = not configured / skipped
    detail: str


def format_report(results: list[CheckResult]) -> str:
    marks = {True: ":white_check_mark:", False: ":x:", None: ":heavy_minus_sign:"}
    return "\n".join(f"{marks[r.ok]} *{r.name}* -- {r.detail}" for r in results)


class HealthChecker:
    def __init__(
        self,
        config: Config,
        remote_factory: Callable[[], Any],
        bridge_state: BridgeState,
        http: Any = requests,
        imap_factory: Callable[[], Any] = default_imap_factory,
        get_sha: Callable[[], str] = get_head_sha,
        describe_models: Callable[[], str] = lambda: "",
        started_at: float | None = None,
        error_counter: Callable[[float], int] = error_count_since,
    ) -> None:
        self._config = config
        self._remote_factory = remote_factory
        self._bridge = bridge_state
        self._http = http
        self._imap_factory = imap_factory
        self._get_sha = get_sha
        self._describe_models = describe_models
        self._started_at = started_at if started_at is not None else time.monotonic()
        self._error_counter = error_counter

    def run(self) -> list[CheckResult]:
        checks: list[tuple[str, Callable[[], CheckResult]]] = [
            ("Fig", self._fig),
            ("OpenRouter", self._openrouter),
            ("Vault", self._vault),
            ("Gmail", self._gmail),
            ("Chrome bridge", self._bridge_check),
            ("Recent errors", self._errors),
        ]
        results = []
        for name, check in checks:
            try:
                results.append(check())
            except Exception as exc:  # noqa: BLE001 - a check must never break the report
                results.append(CheckResult(name, False, f"check crashed: {exc}"))
        return results

    def report(self) -> str:
        return format_report(self.run())

    def _fig(self) -> CheckResult:
        minutes = (time.monotonic() - self._started_at) / 60
        try:
            sha = self._get_sha()[:7]
        except Exception:  # noqa: BLE001
            sha = "unknown"
        models = self._describe_models()
        return CheckResult("Fig", True, f"up {minutes:.0f} min, commit {sha}" + (f", models: {models}" if models else ""))

    def _openrouter(self) -> CheckResult:
        try:
            response = self._http.get(
                OPENROUTER_KEY_URL,
                headers={"Authorization": f"Bearer {self._config.settings.openrouter_api_key}"},
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except Exception as exc:  # noqa: BLE001
            return CheckResult("OpenRouter", False, f"unreachable: {exc}")
        if response.status_code == 200:
            return CheckResult("OpenRouter", True, "API key accepted")
        return CheckResult("OpenRouter", False, f"HTTP {response.status_code} (is the API key valid / credit left?)")

    def _vault(self) -> CheckResult:
        try:
            entries = self._remote_factory().list_dir("")
        except Exception as exc:  # noqa: BLE001
            return CheckResult("Vault", False, f"remote vault not working ({exc}); notes fall back to the local vault")
        return CheckResult("Vault", True, f"remote reachable, {len(entries)} item(s) at the root")

    def _gmail(self) -> CheckResult:
        address, password = self._config.settings.gmail_address, self._config.settings.gmail_app_password
        if not (address and password):
            return CheckResult("Gmail", None, "not configured")
        try:
            with open_mailbox(address, password, self._imap_factory):
                pass
        except GmailError as exc:
            return CheckResult("Gmail", False, str(exc))
        return CheckResult("Gmail", True, f"logged in as {address} (read-only)")

    def _bridge_check(self) -> CheckResult:
        if not self._config.settings.browser_bridge_token:
            return CheckResult("Chrome bridge", None, "off (no BROWSER_BRIDGE_TOKEN)")
        if self._bridge.is_connected():
            return CheckResult("Chrome bridge", True, "extension connected")
        return CheckResult("Chrome bridge", False, "extension hasn't checked in recently (is Chrome open?)")

    def _errors(self) -> CheckResult:
        count = self._error_counter(24)
        if count == 0:
            return CheckResult("Recent errors", True, "none in the last 24h")
        return CheckResult("Recent errors", False, f"{count} in the last 24h -- ask me for `!logs errors`")
