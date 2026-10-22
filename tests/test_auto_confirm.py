from __future__ import annotations

import json
import threading

import pytest

from figai.agent.auto_confirm import RESOLVERS, AutoConfirm
from figai.agent.tools import Tool, ToolRegistry
from figai.tools.confirm import ConfirmationStore
from figai.tools.slack_post import register_slack_post_tool


def make_setup(enabled: bool = True):
    store = ConfirmationStore()
    sent: list[tuple[str, str]] = []
    registry = ToolRegistry()
    register_slack_post_tool(
        registry, store, lambda ch, text, ts: sent.append((ch, text)), lambda: [{"id": "C_GEN", "name": "general"}], "U_OWNER"
    )
    auto = AutoConfirm(store, enabled=enabled)
    registry.result_hook = auto.hook(registry.execute)
    return registry, store, auto, sent


def call(registry: ToolRegistry, name: str, **args):
    return json.loads(registry.execute(name, json.dumps(args)))


def test_without_a_voice_turn_posting_still_asks() -> None:
    registry, _, _, sent = make_setup()
    assert call(registry, "post_slack_message", text="hi", channel="general")["status"] == "awaiting_confirmation"
    assert sent == []


def test_a_voice_turn_posts_without_a_second_yes() -> None:
    registry, _, auto, sent = make_setup()
    with auto.voice_turn():
        result = call(registry, "post_slack_message", text="dinner at 6", channel="general")
    assert sent == [("C_GEN", "dinner at 6")]
    assert result["result"] == {"status": "posted", "to": "#general"}
    assert "out loud" in result["auto_confirmed"]
    # and the guard is off again afterwards
    assert call(registry, "post_slack_message", text="x", channel="general")["status"] == "awaiting_confirmation"


def test_setting_off_means_voice_asks_too() -> None:
    registry, _, auto, sent = make_setup(enabled=False)
    with auto.voice_turn():
        assert call(registry, "post_slack_message", text="x", channel="general")["status"] == "awaiting_confirmation"
    assert sent == []


@pytest.mark.parametrize("action_type", ["memory_delete", "memory_overwrite", "ssh_run", "config_change", "something_new"])
def test_destructive_and_unknown_actions_always_ask(action_type: str) -> None:
    store = ConfirmationStore()
    registry = ToolRegistry()
    ran: list[str] = []

    def propose(_args):
        token = store.propose(action_type, {}, "do the scary thing")
        return json.dumps({"status": "awaiting_confirmation", "token": token, "preview": "do the scary thing"})

    registry.register(Tool(name="scary", description="d", parameters={"type": "object", "properties": {}}, handler=propose))
    for resolver in ("resolve_memory_change", "resolve_memory_write", "resolve_ssh_command", "resolve_config_change"):
        registry.register(
            Tool(
                name=resolver,
                description="d",
                parameters={"type": "object", "properties": {"token": {"type": "string"}}},
                handler=lambda a, r=resolver: ran.append(r) or "{}",
            )
        )
    auto = AutoConfirm(store)
    registry.result_hook = auto.hook(registry.execute)
    with auto.voice_turn():
        assert json.loads(registry.execute("scary", "{}"))["status"] == "awaiting_confirmation"
    assert ran == []


def test_delete_overwrite_ssh_and_settings_are_not_in_the_auto_list() -> None:
    assert not {"memory_delete", "memory_overwrite", "ssh_run", "config_change"} & set(RESOLVERS)


def test_the_voice_turn_only_applies_to_its_own_thread() -> None:
    registry, _, auto, sent = make_setup()
    seen: list[str] = []

    def other_thread() -> None:
        seen.append(call(registry, "post_slack_message", text="x", channel="general")["status"])

    with auto.voice_turn():
        thread = threading.Thread(target=other_thread)
        thread.start()
        thread.join()
    assert seen == ["awaiting_confirmation"] and sent == []


def test_a_crashing_hook_does_not_lose_the_tool_result() -> None:
    registry = ToolRegistry()
    registry.register(Tool(name="t", description="d", parameters={"type": "object", "properties": {}}, handler=lambda a: '{"ok": 1}'))
    registry.result_hook = lambda name, result: 1 / 0
    assert registry.execute("t", "{}") == '{"ok": 1}'


def test_every_auto_resolver_is_a_real_tool_name() -> None:
    # guards against renaming a resolve_* tool and silently losing the shortcut
    import re
    from pathlib import Path

    source = "".join(p.read_text() for p in Path("figai").rglob("*.py"))
    for tool in RESOLVERS.values():
        assert re.search(rf'name="{tool}"', source), tool
