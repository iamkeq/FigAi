"""Atomic JSON file persistence for Fig's lightweight runtime state (active
Slack threads, conversation histories, pending reminders). Not a database --
just enough durability for that state to survive a restart. A corrupt or
missing file is treated as empty state rather than a startup failure."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

from figai.logging_setup import get_logger

logger = get_logger("persistence")


class JsonStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> dict[str, Any]:
        if not self.path.exists():
            return {}
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("Could not read persisted state at %s (%s) -- starting empty", self.path, exc)
            return {}

    def save(self, data: dict[str, Any]) -> None:
        fd, tmp_path = tempfile.mkstemp(dir=self.path.parent, prefix=".tmp-state-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle)
            os.replace(tmp_path, self.path)
        except Exception:
            if os.path.exists(tmp_path):
                os.unlink(tmp_path)
            raise
