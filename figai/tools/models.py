"""Switch Fig's models from chat: get_models / set_model / reset_model, plus the
deterministic `!model` command (which still works when the current model is the
thing that's broken). Roles: chat (the main model), vision (screenshots), email
(private email summaries). Changes apply immediately, are validated against
OpenRouter's catalog first, and persist across restarts."""

from __future__ import annotations

import difflib
import json
import time
from typing import Any, Callable

import requests

from figai.agent.tools import Tool, ToolRegistry
from figai.config import Config

CATALOG_URL = "https://openrouter.ai/api/v1/models"
CATALOG_TTL_SECONDS = 600
ROLES = {"chat": "default_chat", "vision": "vision", "email": "email"}


class ModelError(Exception):
    pass


class ModelManager:
    def __init__(
        self,
        config: Config,
        apply_chat: Callable[[str], None],
        on_change: Callable[[dict[str, str]], None] | None = None,
        http: Any = requests,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._models = config.app.models
        self._apply_chat = apply_chat
        self.on_change = on_change
        self._http = http
        self._clock = clock
        self._defaults = {role: getattr(self._models, field) for role, field in ROLES.items()}
        self._catalog: set[str] | None = None
        self._catalog_at = 0.0

    def current(self) -> dict[str, str | None]:
        return {role: getattr(self._models, field) for role, field in ROLES.items()}

    def overrides(self) -> dict[str, str]:
        return {r: m for r, m in self.current().items() if m != self._defaults[r] and m is not None}

    def _catalog_ids(self) -> set[str]:
        if self._catalog is None or self._clock() - self._catalog_at > CATALOG_TTL_SECONDS:
            try:
                response = self._http.get(CATALOG_URL, timeout=15)
                response.raise_for_status()
                self._catalog = {m["id"] for m in response.json()["data"]}
            except Exception as exc:  # noqa: BLE001
                raise ModelError(f"Couldn't check OpenRouter's model list, so I changed nothing: {exc}") from exc
            self._catalog_at = self._clock()
        return self._catalog

    def _assign(self, role: str, model: str | None) -> None:
        setattr(self._models, ROLES[role], model)
        if role == "chat" and model:
            self._apply_chat(model)

    def _changed(self) -> None:
        if self.on_change:
            self.on_change(self.overrides())

    def _check_role(self, role: str) -> None:
        if role not in ROLES:
            raise ModelError(f"Unknown role '{role}'. Use one of: {', '.join(ROLES)}.")

    def set(self, role: str, model: str) -> str:
        self._check_role(role)
        model = model.strip()
        if not model or any(c.isspace() for c in model):
            raise ModelError("That doesn't look like a model id (expected e.g. 'provider/model-name').")
        if role == "email" and model.endswith(":free"):
            raise ModelError(
                "Free models can log prompts, so they can't be the email model. Pick a paid model "
                "(for example anthropic/claude-haiku-5.5)."
            )
        ids = self._catalog_ids()
        if model not in ids:
            close = difflib.get_close_matches(model, sorted(ids), n=3)
            hint = f" Did you mean: {', '.join(close)}?" if close else ""
            raise ModelError(f"'{model}' isn't in OpenRouter's catalog, so I changed nothing.{hint}")
        previous = self.current()[role]
        self._assign(role, model)
        self._changed()
        return f"{role} model is now {model} (was {previous or 'not set'})."

    def reset(self, role: str) -> str:
        self._check_role(role)
        default = self._defaults[role]
        self._assign(role, default)
        self._changed()
        return f"{role} model reset to {default or 'not set'}."

    def restore(self, overrides: dict[str, str]) -> None:
        """Re-applies saved overrides at startup (already validated when set)."""
        for role, model in overrides.items():
            if role in ROLES and model:
                self._assign(role, model)

    def describe(self) -> str:
        return "; ".join(f"{role}: {model or 'not set'}" for role, model in self.current().items())


def register_model_tools(registry: ToolRegistry, manager: ModelManager) -> None:
    def _get(_arguments: dict[str, Any]) -> str:
        return json.dumps({"models": manager.current(), "overrides": manager.overrides()})

    registry.register(
        Tool(
            name="get_models",
            description="Show which models Fig is using for chat, vision (screenshots), and email summaries.",
            parameters={"type": "object", "properties": {}},
            handler=_get,
        )
    )

    def _set(arguments: dict[str, Any]) -> str:
        try:
            return json.dumps({"status": manager.set(arguments.get("role", "chat"), arguments["model"])})
        except ModelError as exc:
            return json.dumps({"error": str(exc)})

    registry.register(
        Tool(
            name="set_model",
            description=(
                "Switch a model, effective immediately and kept across restarts. role is 'chat' (the main "
                "model, default), 'vision' (screenshots), or 'email' (private email summaries; must be a "
                "paid model). model is an OpenRouter id like 'anthropic/claude-haiku-5.5'; it is checked "
                "against OpenRouter's catalog first."
            ),
            parameters={
                "type": "object",
                "properties": {
                    "model": {"type": "string"},
                    "role": {"type": "string", "enum": list(ROLES)},
                },
                "required": ["model"],
            },
            handler=_set,
        )
    )

    def _reset(arguments: dict[str, Any]) -> str:
        try:
            return json.dumps({"status": manager.reset(arguments.get("role", "chat"))})
        except ModelError as exc:
            return json.dumps({"error": str(exc)})

    registry.register(
        Tool(
            name="reset_model",
            description="Put a model role back to the default from config.yaml. role: chat (default), vision, or email.",
            parameters={"type": "object", "properties": {"role": {"type": "string", "enum": list(ROLES)}}},
            handler=_reset,
        )
    )
