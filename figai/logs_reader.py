"""Reads Fig's own rotating log so problems can be inspected from chat (the `!logs`
command and the recent_logs tool) without needing the PC in front of you.
Log lines look like: `2026-10-21 20:29:57 WARNING  figai.agent.loop: message`;
tracebacks are the unstamped lines that follow an ERROR record."""

from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path

from figai.logging_setup import LOG_FILE

RECORD_START = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\w+)\s+(\S+?):")
READ_TAIL_BYTES = 400_000
MAX_LINE_CHARS = 300
FILTERS = ("errors", "warnings", "tools", "all")
_LEVELS = {"errors": {"ERROR", "CRITICAL"}, "warnings": {"WARNING", "ERROR", "CRITICAL"}}


def _records(path: Path) -> list[tuple[datetime | None, str, str, list[str]]]:
    """(timestamp, level, logger, [header line, *continuation lines]) for the tail of the log."""
    if not path.exists():
        return []
    with path.open("rb") as handle:
        handle.seek(0, 2)
        size = handle.tell()
        handle.seek(max(0, size - READ_TAIL_BYTES))
        text = handle.read().decode("utf-8", errors="replace")
    records: list[tuple[datetime | None, str, str, list[str]]] = []
    for line in text.splitlines():
        match = RECORD_START.match(line)
        if match:
            stamp = datetime.strptime(match.group(1), "%Y-%m-%d %H:%M:%S")
            records.append((stamp, match.group(2), match.group(3), [line]))
        elif records and line.strip():
            records[-1][3].append(line)
    return records


def _short(line: str) -> str:
    return line if len(line) <= MAX_LINE_CHARS else line[: MAX_LINE_CHARS - 1] + "…"


def recent_lines(kind: str = "errors", count: int = 15, path: Path = LOG_FILE) -> list[str]:
    """The last `count` matching records, oldest first. For errors the exception's final
    line is included so you see *what* failed, not just that something did."""
    if kind not in FILTERS:
        raise ValueError(f"kind must be one of {', '.join(FILTERS)}")
    count = max(1, min(count, 50))
    picked: list[str] = []
    for _stamp, level, logger_name, lines in _records(path):
        if kind in _LEVELS and level not in _LEVELS[kind]:
            continue
        if kind == "tools" and not logger_name.endswith("tools"):
            continue
        entry = _short(lines[0])
        if len(lines) > 1 and level in _LEVELS["errors"]:
            entry += "\n    " + _short(lines[-1].strip())
        picked.append(entry)
    return picked[-count:]


def error_count_since(hours: float = 24, path: Path = LOG_FILE, now: datetime | None = None) -> int:
    cutoff = (now or datetime.now()) - timedelta(hours=hours)
    return sum(
        1
        for stamp, level, _name, _lines in _records(path)
        if level in _LEVELS["errors"] and stamp is not None and stamp >= cutoff
    )


def last_error(path: Path = LOG_FILE) -> str | None:
    errors = recent_lines("errors", 1, path)
    return errors[0] if errors else None
