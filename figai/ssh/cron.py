"""Pure crontab editing. Fig only ever touches lines he owns -- those ending in `# fig:<name>` --
and leaves every other line byte-for-byte as it was. A disabled job is kept as a comment
(`#DISABLED# ...`) so it can be switched back on without retyping it."""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_COMMAND_CHARS = 500
NAME_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,39}$")
MANAGED = re.compile(r"^(?P<disabled>#DISABLED# )?(?P<body>.*?)\s+# fig:(?P<name>[A-Za-z0-9_-]+)\s*$")
FIELD = re.compile(r"^[0-9A-Za-z*/,\-?]+$")
SPECIALS = {"@reboot", "@hourly", "@daily", "@midnight", "@weekly", "@monthly", "@yearly", "@annually"}


class CronError(Exception):
    pass


@dataclass
class CronEntry:
    name: str | None  # None for lines Fig doesn't own
    schedule: str
    command: str
    enabled: bool
    raw: str

    @property
    def managed(self) -> bool:
        return self.name is not None


def validate_schedule(schedule: str) -> str:
    schedule = " ".join(schedule.split())
    if schedule.lower() in SPECIALS:
        return schedule.lower()
    fields = schedule.split(" ")
    if len(fields) != 5 or not all(FIELD.match(f) for f in fields):
        raise CronError(
            "A schedule is 5 fields (minute hour day-of-month month day-of-week), e.g. '0 4 * * *' for 4 AM "
            f"daily, or one of {', '.join(sorted(SPECIALS))}. Got: '{schedule}'"
        )
    return schedule


def validate_command(command: str) -> str:
    command = command.strip()
    if not command:
        raise CronError("The command is empty.")
    if "\n" in command or "\r" in command:
        raise CronError("A cron job's command must be a single line (put multi-step jobs in a script).")
    if len(command) > MAX_COMMAND_CHARS:
        raise CronError(f"Command too long ({len(command)} chars; max {MAX_COMMAND_CHARS}).")
    return re.sub(r"(?<!\\)%", r"\\%", command)  # an unescaped % means a newline to cron


def parse(crontab: str) -> list[CronEntry]:
    entries: list[CronEntry] = []
    for line in crontab.splitlines():
        match = MANAGED.match(line)
        if match:
            body = match.group("body").strip()
            if body.startswith("@"):
                schedule, _, command = body.partition(" ")
            else:
                parts = body.split(None, 5)
                schedule, command = " ".join(parts[:5]), (parts[5] if len(parts) > 5 else "")
            entries.append(CronEntry(match.group("name"), schedule, command, not match.group("disabled"), line))
            continue
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" in stripped.split(None, 1)[0]:
            continue  # comments, blanks and VAR=value lines aren't jobs
        parts = stripped.split(None, 5)
        if stripped.startswith("@"):
            schedule, _, command = stripped.partition(" ")
        else:
            schedule, command = " ".join(parts[:5]), (parts[5] if len(parts) > 5 else "")
        entries.append(CronEntry(None, schedule, command, True, line))
    return entries


def _line(name: str, schedule: str, command: str, enabled: bool) -> str:
    return f"{'' if enabled else '#DISABLED# '}{schedule} {command} # fig:{name}"


def _find(lines: list[str], name: str) -> int | None:
    for i, line in enumerate(lines):
        match = MANAGED.match(line)
        if match and match.group("name").lower() == name.lower():
            return i
    return None


def add_job(crontab: str, name: str, schedule: str, command: str) -> str:
    if not NAME_PATTERN.match(name):
        raise CronError("Job name: 1-40 letters, digits, dashes or underscores (no spaces).")
    schedule, command = validate_schedule(schedule), validate_command(command)
    lines = crontab.splitlines()
    if _find(lines, name) is not None:
        raise CronError(f"A Fig job named '{name}' already exists; remove it first to change it.")
    lines.append(_line(name, schedule, command, True))
    return "\n".join(lines) + "\n"


def remove_job(crontab: str, name: str) -> str:
    lines = crontab.splitlines()
    index = _find(lines, name)
    if index is None:
        raise CronError(f"No Fig-managed job named '{name}'. (Fig only edits jobs he created, tagged '# fig:<name>'.)")
    del lines[index]
    return "\n".join(lines) + ("\n" if lines else "")


def set_enabled(crontab: str, name: str, enabled: bool) -> str:
    lines = crontab.splitlines()
    index = _find(lines, name)
    if index is None:
        raise CronError(f"No Fig-managed job named '{name}'. (Fig only edits jobs he created, tagged '# fig:<name>'.)")
    match = MANAGED.match(lines[index])
    assert match is not None
    lines[index] = _line(match.group("name"), *_split(match.group("body")), enabled)
    return "\n".join(lines) + "\n"


def _split(body: str) -> tuple[str, str]:
    body = body.strip()
    if body.startswith("@"):
        schedule, _, command = body.partition(" ")
        return schedule, command
    parts = body.split(None, 5)
    return " ".join(parts[:5]), (parts[5] if len(parts) > 5 else "")
