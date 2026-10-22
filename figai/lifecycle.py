"""Knows how Fig last stopped, so an unexpected stop (crash, power loss, killed process)
or a failed unattended update gets reported to Slack at the next start instead of
going unnoticed.

- A "running" marker file exists while Fig runs. A clean exit, or a restart Fig does
  itself, removes it; the scripts remove it when they stop Fig on purpose. A marker
  still present at startup therefore means the last run ended unexpectedly.
- update_and_restart.ps1 leaves data/update_result.json when an update failed or was
  rolled back; Fig reads and removes it at startup."""

from __future__ import annotations

import atexit
import json
from pathlib import Path
from typing import Any

from figai.logging_setup import get_logger

logger = get_logger("lifecycle")

_marker: Path | None = None


def mark_started(marker: Path) -> bool:
    """Records that Fig is running. Returns True if the previous run ended unexpectedly."""
    global _marker
    unclean = marker.exists()
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text("running", encoding="utf-8")
    _marker = marker
    atexit.register(mark_clean_stop)
    return unclean


def mark_clean_stop() -> None:
    """Call before any deliberate exit or os.execv restart (execv skips atexit)."""
    if _marker is not None:
        try:
            _marker.unlink(missing_ok=True)
        except OSError:
            logger.exception("Could not remove the running marker")


def take_update_result(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        result = json.loads(path.read_text(encoding="utf-8-sig"))  # PowerShell may write a BOM
    except (ValueError, OSError):
        logger.exception("Unreadable update result at %s", path)
        result = {"status": "unknown", "detail": "update_result.json was unreadable"}
    try:
        path.unlink()
    except OSError:
        logger.exception("Could not remove %s", path)
    return result if isinstance(result, dict) else None


def build_startup_alerts(
    unclean_stop: bool, update_result: dict[str, Any] | None, last_error: str | None
) -> list[str]:
    alerts: list[str] = []
    if update_result:
        status = update_result.get("status")
        when = update_result.get("time", "")
        detail = update_result.get("detail", "")
        if status == "rolled_back":
            alerts.append(
                f":warning: The unattended update ({when}) broke Fig, so it rolled back to the previous version "
                f"({str(update_result.get('previous', ''))[:7]}). The new commit "
                f"({str(update_result.get('attempted', ''))[:7]}) needs a look -- see logs\\update.log and logs\\figai.log. {detail}".strip()
            )
        elif status == "pull_failed":
            alerts.append(f":warning: The unattended update ({when}) couldn't pull from GitHub, so Fig kept running the old code. {detail}".strip())
        elif status == "rollback_failed":
            alerts.append(f":rotating_light: The update ({when}) failed and the rollback failed too. {detail}".strip())
        elif status == "install_failed":
            alerts.append(f":warning: The update ({when}) pulled new code but `pip install` failed; Fig started anyway. {detail}".strip())
        else:
            alerts.append(f":warning: The last unattended update left a note I couldn't interpret ({status}). {detail}".strip())
    if unclean_stop:
        message = ":warning: Fig's previous run ended unexpectedly (crash, killed process, or power loss)."
        if last_error:
            message += f" Last error in the log: `{last_error.splitlines()[0][:200]}`"
        alerts.append(message)
    return alerts
