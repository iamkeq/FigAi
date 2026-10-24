"""Shared two-step confirmation primitive for side-effectful tools (ssh_run, send_file,
writes outside the vault's writable subfolder, deletes/overwrites). A tool proposes an
action and gets a token back; nothing happens until a later call resolves that token --
front-ends are responsible for only resolving after the user explicitly confirms in a
LATER message, never the one that proposed it."""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class PendingAction:
    action_type: str
    payload: dict[str, Any]
    preview: str
    created_at: float


class ConfirmationStore:
    def __init__(self, timeout_seconds: int = 300) -> None:
        self._pending: dict[str, PendingAction] = {}
        self.timeout_seconds = timeout_seconds

    def propose(self, action_type: str, payload: dict[str, Any], preview: str) -> str:
        token = secrets.token_hex(4)
        self._pending[token] = PendingAction(action_type, payload, preview, time.monotonic())
        return token

    def latest(self) -> tuple[str, str, str] | None:
        """(token, action type, preview) of the most recent pending action that hasn't expired."""
        if not self._pending:
            return None
        token = next(reversed(self._pending))
        action = self._pending[token]
        if time.monotonic() - action.created_at > self.timeout_seconds:
            return None
        return token, action.action_type, action.preview

    def cancel(self, token: str) -> bool:
        return self._pending.pop(token, None) is not None

    def peek_type(self, token: str) -> str | None:
        """The action type of a pending token (None if unknown), without consuming it."""
        action = self._pending.get(token)
        return action.action_type if action else None

    def resolve(self, token: str, expected_type: str) -> dict[str, Any]:
        action = self._pending.get(token)
        if action is None:
            raise KeyError(f"No pending confirmation for token '{token}'")
        if action.action_type != expected_type:
            raise KeyError(f"Token '{token}' is not a pending '{expected_type}' action")
        del self._pending[token]
        if time.monotonic() - action.created_at > self.timeout_seconds:
            raise TimeoutError(f"Confirmation token '{token}' expired; propose the action again")
        return action.payload
