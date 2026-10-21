"""propose_update_fig / resolve_update_fig: pulls the latest code from GitHub,
reinstalls dependencies, and restarts Fig to load it. Confirm-gated since it
changes Fig's own code and restarts the whole process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry
from figai.logging_setup import get_logger
from figai.process_utils import quiet_run
from figai.reminders.scheduler import TimerFactory, default_timer_factory
from figai.tools.confirm import ConfirmationStore
from figai.update.github_info import get_head_sha

logger = get_logger("tools.self_update")
ACTION_TYPE = "update_fig"
RESTART_DELAY_SECONDS = 2


class UpdateError(Exception):
    pass


def default_restart() -> None:
    os.execv(sys.executable, [sys.executable, "-m", "figai"])


def pull_and_reinstall(runner: Callable[..., Any] = quiet_run) -> None:
    try:
        runner(["git", "pull"], check=True, capture_output=True, text=True, timeout=60)
        runner(
            [sys.executable, "-m", "pip", "install", "-e", ".[dev]"],
            check=True,
            capture_output=True,
            text=True,
            timeout=300,
        )
    except subprocess.CalledProcessError as exc:
        raise UpdateError((exc.stderr or str(exc))[-500:]) from exc
    except Exception as exc:  # noqa: BLE001 - e.g. git not on PATH, timeout
        raise UpdateError(str(exc)) from exc


def register_self_update_tools(
    registry: ToolRegistry,
    confirmations: ConfirmationStore,
    runner: Callable[..., Any] = quiet_run,
    restart: Callable[[], None] = default_restart,
    timer_factory: TimerFactory = default_timer_factory,
    get_sha: Callable[[], str] = get_head_sha,
    on_updated: Callable[[str, str], None] | None = None,
) -> None:
    def _propose(_arguments: dict[str, Any]) -> str:
        preview = "Pull the latest code from GitHub, reinstall dependencies, and restart Fig."
        token = confirmations.propose(ACTION_TYPE, {}, preview)
        logger.info("Proposed self-update token=%s", token)
        return json.dumps(
            {
                "status": "awaiting_confirmation",
                "token": token,
                "preview": preview,
                "note": "Wait for the user's explicit yes in a LATER message before calling resolve_update_fig.",
            }
        )

    registry.register(
        Tool(
            name="propose_update_fig",
            description=(
                "Draft updating Fig: pulling the latest code from GitHub, reinstalling dependencies, and "
                "restarting to load it. Does nothing until the user explicitly confirms in a later message."
            ),
            parameters={"type": "object", "properties": {}},
            handler=_propose,
        )
    )

    def _resolve(arguments: dict[str, Any]) -> str:
        token = arguments["token"]
        try:
            confirmations.resolve(token, ACTION_TYPE)
        except (KeyError, TimeoutError) as exc:
            return json.dumps({"error": str(exc)})

        try:
            old_sha = get_sha()
        except Exception:  # noqa: BLE001 - never block an update over a sha lookup failure
            old_sha = None

        try:
            pull_and_reinstall(runner)
        except UpdateError as exc:
            return json.dumps({"error": f"Update failed, still running the old code: {exc}"})

        if on_updated is not None:
            try:
                new_sha = get_sha()
                if new_sha and new_sha != old_sha:
                    on_updated(old_sha or "", new_sha)
            except Exception:  # noqa: BLE001 - the restart must still happen either way
                logger.exception("Failed to record update details")

        logger.info("Update pulled successfully; restarting in %ds", RESTART_DELAY_SECONDS)
        # Delayed so the agent loop can finish this turn and the front-end can
        # actually send the reply before the process image gets replaced.
        timer_factory(RESTART_DELAY_SECONDS, restart)
        return json.dumps(
            {"status": "updated", "note": f"Restarting in {RESTART_DELAY_SECONDS}s to load the new code."}
        )

    registry.register(
        Tool(
            name="resolve_update_fig",
            description=(
                "Actually pull the update and restart, after the user has explicitly confirmed in a later "
                "message. Never call this in the same turn the update was proposed."
            ),
            parameters={"type": "object", "properties": {"token": {"type": "string"}}, "required": ["token"]},
            handler=_resolve,
        )
    )
