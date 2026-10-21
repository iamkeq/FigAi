"""open_program: launches an allowlisted executable by friendly name. No arbitrary
shell commands -- only paths explicitly mapped in config.yaml can ever run."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Callable

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config
from figai.logging_setup import get_logger

logger = get_logger("tools.programs")

Launcher = Callable[[str], None]


def default_launcher(path: str) -> None:
    subprocess.Popen([path], close_fds=True)  # noqa: S603 - path comes only from the allowlist


def register_open_program_tool(registry: ToolRegistry, config: Config, launcher: Launcher = default_launcher) -> None:
    def _open_program(arguments: dict[str, Any]) -> str:
        name = arguments["name"].strip().lower()
        allowlist = config.app.allowlisted_programs
        if name not in allowlist:
            return json.dumps({"error": f"'{name}' is not allowlisted. Configured programs: {sorted(allowlist)}"})

        path = allowlist[name]
        if not Path(path).exists():
            return json.dumps({"error": f"Configured path for '{name}' does not exist: {path}"})

        try:
            launcher(path)
        except OSError as exc:
            logger.error("Failed to launch %s (%s): %s", name, path, exc)
            return json.dumps({"error": str(exc)})

        logger.info("Launched allowlisted program '%s'", name)
        return json.dumps({"status": "launched", "program": name})

    registry.register(
        Tool(
            name="open_program",
            description="Launch a program by its friendly name, from the allowlist configured in config.yaml.",
            parameters={"type": "object", "properties": {"name": {"type": "string"}}, "required": ["name"]},
            handler=_open_program,
        )
    )
