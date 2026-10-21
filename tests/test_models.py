from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from figai.agent.tools import ToolRegistry
from figai.tools.models import ModelError, ModelManager, register_model_tools


class FakeResponse:
    def __init__(self, ids: list[str], ok: bool = True) -> None:
        self._ids, self._ok = ids, ok

    def raise_for_status(self) -> None:
        if not self._ok:
            raise RuntimeError("down")

    def json(self):
        return {"data": [{"id": i} for i in self._ids]}


class FakeHttp:
    def __init__(self, ids, ok=True) -> None:
        self.ids, self.ok, self.fetches = ids, ok, 0

    def get(self, url, *, timeout):
        self.fetches += 1
        return FakeResponse(self.ids, self.ok)


CATALOG = ["a/chat-1", "a/chat-2", "b/vision-1", "anthropic/claude-haiku-5.5", "x/free-one:free"]


def make_manager(http=None, **models):
    values = {"default_chat": "a/chat-1", "vision": "b/vision-1", "email": None, **models}
    config = SimpleNamespace(app=SimpleNamespace(models=SimpleNamespace(**values)))
    applied: list[str] = []
    saved: list[dict] = []
    manager = ModelManager(config, apply_chat=applied.append, on_change=saved.append, http=http or FakeHttp(CATALOG))
    return manager, config.app.models, applied, saved


def test_set_chat_model_applies_immediately_and_persists() -> None:
    manager, models, applied, saved = make_manager()

    message = manager.set("chat", "a/chat-2")

    assert models.default_chat == "a/chat-2" and applied == ["a/chat-2"]
    assert saved == [{"chat": "a/chat-2"}]
    assert "a/chat-1" in message


def test_set_rejects_unknown_model_with_suggestions_and_changes_nothing() -> None:
    manager, models, applied, saved = make_manager()

    with pytest.raises(ModelError) as excinfo:
        manager.set("chat", "a/chat-9")

    assert "a/chat-" in str(excinfo.value)
    assert models.default_chat == "a/chat-1" and applied == [] and saved == []


def test_set_fails_closed_when_the_catalog_is_unreachable() -> None:
    manager, models, _, _ = make_manager(http=FakeHttp([], ok=False))
    with pytest.raises(ModelError):
        manager.set("chat", "a/chat-2")
    assert models.default_chat == "a/chat-1"


def test_catalog_is_cached_between_calls() -> None:
    http = FakeHttp(CATALOG)
    manager, *_ = make_manager(http=http)
    manager.set("chat", "a/chat-2")
    manager.set("vision", "b/vision-1")
    assert http.fetches == 1


def test_email_role_refuses_free_models() -> None:
    manager, models, _, _ = make_manager()
    with pytest.raises(ModelError):
        manager.set("email", "x/free-one:free")
    assert models.email is None


def test_email_role_accepts_a_paid_model_without_touching_the_chat_loop() -> None:
    manager, models, applied, _ = make_manager()
    manager.set("email", "anthropic/claude-haiku-5.5")
    assert models.email == "anthropic/claude-haiku-5.5" and applied == []


def test_unknown_role_and_malformed_ids_are_rejected() -> None:
    manager, *_ = make_manager()
    with pytest.raises(ModelError):
        manager.set("nonsense", "a/chat-2")
    with pytest.raises(ModelError):
        manager.set("chat", "two words")


def test_reset_restores_the_config_default() -> None:
    manager, models, applied, saved = make_manager()
    manager.set("chat", "a/chat-2")

    manager.reset("chat")

    assert models.default_chat == "a/chat-1" and applied[-1] == "a/chat-1" and saved[-1] == {}


def test_restore_reapplies_saved_overrides_to_config_and_loop() -> None:
    manager, models, applied, _ = make_manager()
    manager.restore({"chat": "a/chat-2", "email": "anthropic/claude-haiku-5.5", "bogus": "x"})
    assert models.default_chat == "a/chat-2" and models.email == "anthropic/claude-haiku-5.5"
    assert applied == ["a/chat-2"]


def test_tools_round_trip() -> None:
    manager, models, _, _ = make_manager()
    registry = ToolRegistry()
    register_model_tools(registry, manager)

    assert json.loads(registry.execute("get_models", "{}"))["models"]["chat"] == "a/chat-1"
    assert "status" in json.loads(registry.execute("set_model", json.dumps({"model": "a/chat-2"})))
    assert models.default_chat == "a/chat-2"
    assert "error" in json.loads(registry.execute("set_model", json.dumps({"model": "nope/nope"})))
    assert "status" in json.loads(registry.execute("reset_model", "{}"))
    assert models.default_chat == "a/chat-1"
