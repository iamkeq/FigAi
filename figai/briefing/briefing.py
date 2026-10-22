"""The daily briefing: one Slack message each morning. Built mostly WITHOUT a model --
reminders, down checks, vault notes dated today and health problems are plain lookups. The
only token spend is the single email-summary call (and only if include_email is on), so
the cost is small and predictable. `!briefing off` stops it entirely."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime, time as dt_time, timedelta
from typing import Any, Callable

from figai.config import BriefingConfig
from figai.logging_setup import get_logger

logger = get_logger("briefing")

CATCH_UP_HOURS = 6  # if the PC was off at briefing time, still send it when it comes back, but not at night
TIME_PATTERN = re.compile(r"^([01]?\d|2[0-3]):([0-5]\d)$")
VAULT_HITS = 5

RunTool = Callable[[str, dict[str, Any]], dict[str, Any]]


@dataclass
class BriefingSources:
    """Everything the briefing reads, injected so it's testable without any I/O."""

    run_tool: RunTool
    pending_reminders: Callable[[], list[Any]]  # objects with .message and .due_at (epoch seconds)
    down_checks: Callable[[], list[str]]
    health_problems: Callable[[], list[str]]


def build_briefing(sources: BriefingSources, settings: BriefingConfig, now: datetime) -> str:
    sections: list[str] = [f"*Good morning! Your briefing for {now:%A, %B} {now.day}*"]

    start, end = datetime.combine(now.date(), dt_time.min), datetime.combine(now.date(), dt_time.max)
    todays = [
        r for r in sources.pending_reminders() if start.timestamp() <= r.due_at <= end.timestamp()
    ]
    if todays:
        lines = [f"- {datetime.fromtimestamp(r.due_at):%H:%M} {r.message}" for r in todays]
        sections.append("*Reminders today*\n" + "\n".join(lines))

    down = sources.down_checks()
    if down:
        sections.append(":rotating_light: *Down right now*\n" + "\n".join(f"- {d}" for d in down))

    if settings.include_email:
        result = sources.run_tool(
            "summarize_emails",
            {
                "query": "in:inbox newer_than:1d",
                "limit": settings.email_limit,
                "focus": "What is urgent or needs a reply or action today?",
            },
        )
        if "summary" in result:
            sections.append("*Email (last 24h)*\n" + str(result["summary"]).strip())
        else:
            sections.append(f"*Email* -- skipped: {str(result.get('error', 'unavailable'))[:200]}")

    hits: dict[str, str] = {}
    for query in (now.date().isoformat(), "#today"):
        for hit in sources.run_tool("memory_search", {"query": query}).get("results", []):
            hits.setdefault(hit["path"], hit.get("snippet", ""))
    if hits:
        lines = [f"- `{path}` -- {snip[:100]}" for path, snip in list(hits.items())[:VAULT_HITS]]
        sections.append("*Vault notes for today*\n" + "\n".join(lines))

    problems = sources.health_problems()
    if problems:
        sections.append(":warning: *Needs attention*\n" + "\n".join(f"- {p}" for p in problems))

    if len(sections) == 1:
        sections.append("Nothing scheduled and nothing needs attention.")
    return "\n\n".join(sections)


def parse_time(text: str) -> str:
    match = TIME_PATTERN.match(text.strip())
    if not match:
        raise ValueError("Time must look like 07:30 (24-hour, your PC's local time).")
    return f"{int(match.group(1)):02d}:{match.group(2)}"


class BriefingManager:
    def __init__(
        self,
        defaults: BriefingConfig,
        owner_channel: str,
        build: Callable[[BriefingConfig, datetime], str],
        notify: Callable[[str, str], None],
        now: Callable[[], datetime] = datetime.now,
        on_change: Callable[[], None] | None = None,
    ) -> None:
        self.settings = defaults.model_copy()
        self._owner_channel = owner_channel
        self._build = build
        self._notify = notify
        self._now = now
        self.on_change = on_change
        self.last_run: str | None = None  # ISO date of the last briefing sent

    def configure(
        self,
        enabled: bool | None = None,
        time: str | None = None,
        include_email: bool | None = None,
        channel: str | None = None,
    ) -> None:
        if time is not None:
            self.settings.time = parse_time(time)
        if enabled is not None:
            self.settings.enabled = enabled
        if include_email is not None:
            self.settings.include_email = include_email
        if channel is not None:
            self.settings.channel = channel or None
        self._changed()

    def tick(self) -> None:
        if not self.settings.enabled:
            return
        now = self._now()
        hour, minute = (int(p) for p in self.settings.time.split(":"))
        scheduled = datetime.combine(now.date(), dt_time(hour, minute))
        if self.last_run == now.date().isoformat() or not (scheduled <= now < scheduled + timedelta(hours=CATCH_UP_HOURS)):
            return
        self.last_run = now.date().isoformat()  # recorded first, so a slow send can't fire twice
        self._changed()
        self.send_now()

    def send_now(self) -> str:
        text = self._build(self.settings, self._now())
        self._notify(self.settings.channel or self._owner_channel, text)
        return text

    def status(self) -> str:
        s = self.settings
        if not s.enabled:
            return "The daily briefing is *off*. Turn it on with `!briefing on`."
        email = f"on (one email-model call, up to {s.email_limit} emails)" if s.include_email else "off"
        return (
            f"The daily briefing is *on*: every day at {s.time} (local time), to "
            f"{'<#' + s.channel + '>' if s.channel else 'your DM'}. Email section: {email}. "
            f"Last sent: {self.last_run or 'never'}. `!briefing off` stops it; `!briefing now` sends one now."
        )

    def to_state(self) -> dict[str, Any]:
        return {**self.settings.model_dump(), "last_run": self.last_run}

    def restore(self, state: dict[str, Any]) -> None:
        if not state:
            return
        self.last_run = state.get("last_run")
        try:
            self.settings = BriefingConfig(**{k: v for k, v in state.items() if k != "last_run"})
        except ValueError:
            logger.warning("Ignoring unreadable saved briefing settings: %s", state)

    def _changed(self) -> None:
        if self.on_change:
            self.on_change()
