from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from figai.config import AppConfig
from figai.frontends.slack_router import (
    RESTART_SENTINEL,
    SlackRouter,
    is_authorized_message,
    strip_mentions,
)
from figai.reminders.context import ConversationContext
from figai.skills_system.loader import SkillLoader


class FakeLoop:
    def __init__(self, reply: str = "ok", raise_error: bool = False) -> None:
        self.reply = reply
        self.raise_error = raise_error
        self.calls: list[list[dict]] = []

    def run(self, conversation: list[dict]) -> str:
        self.calls.append(list(conversation))
        if self.raise_error:
            raise RuntimeError("boom")
        return self.reply


def make_app_config(tmp_path: Path) -> SimpleNamespace:
    app = AppConfig.model_validate(
        {
            "models": {"default_chat": "test/model", "vision": "y"},
            "vault": {"ssh_host_alias": "v", "remote_path": "/v", "local_path": str(tmp_path / "vault")},
        }
    )
    return SimpleNamespace(app=app)


def make_router(tmp_path: Path, reply: str = "ok", raise_error: bool = False) -> SlackRouter:
    loader = SkillLoader(tmp_path / "skills")
    session = SimpleNamespace(
        loop=FakeLoop(reply, raise_error),
        loader=loader,
        conversation_context=ConversationContext(),
        health=SimpleNamespace(report=lambda: ":white_check_mark: *Fig* -- fine"),
    )
    return SlackRouter(session=session, config=make_app_config(tmp_path), allowed_user_id="U_OWNER")


def dm_event(text: str, channel: str = "D1") -> dict:
    return {"user": "U_OWNER", "text": text, "channel": channel, "channel_type": "im"}


def channel_event(text: str, channel: str = "C1", thread_ts: str | None = None) -> dict:
    event: dict = {"user": "U_OWNER", "text": text, "channel": channel, "channel_type": "channel"}
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    return event


def mention_event(text: str, channel: str = "C1", ts: str = "100.1", thread_ts: str | None = None) -> dict:
    event = {"user": "U_OWNER", "text": f"<@UBOT123> {text}", "channel": channel, "ts": ts}
    if thread_ts is not None:
        event["thread_ts"] = thread_ts
    return event


def test_is_authorized_message_accepts_only_the_allowed_user() -> None:
    assert is_authorized_message({"user": "U_OWNER"}, "U_OWNER") is True
    assert is_authorized_message({"user": "U_OTHER"}, "U_OWNER") is False


def test_is_authorized_message_ignores_bot_messages() -> None:
    assert is_authorized_message({"user": "U_OWNER", "bot_id": "B1"}, "U_OWNER") is False


def test_is_authorized_message_ignores_message_subtypes() -> None:
    assert is_authorized_message({"user": "U_OWNER", "subtype": "message_changed"}, "U_OWNER") is False


def test_strip_mentions_removes_user_mentions() -> None:
    assert strip_mentions("<@U123ABC> what's up") == "what's up"
    assert strip_mentions("hello <@U123ABC>") == "hello"


# -- DMs ----------------------------------------------------------------------


def test_handle_event_ignores_unauthorized_sender_in_dm(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    event = dm_event("hi")
    event["user"] = "U_OTHER"
    assert router.handle_event(event) is None


def test_handle_event_ignores_empty_text_in_dm(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    assert router.handle_event(dm_event("   ")) is None


def test_handle_event_dm_routes_to_agent_loop(tmp_path: Path) -> None:
    router = make_router(tmp_path, reply="hello back")

    reply = router.handle_event(dm_event("hi"))

    assert reply is not None
    assert reply.text == "hello back"
    assert reply.thread_ts is None
    assert router.conversations["D1"][-1] == {"role": "assistant", "content": "hello back"}


def test_handle_event_dm_agent_loop_error_rolls_back_conversation(tmp_path: Path) -> None:
    router = make_router(tmp_path, raise_error=True)

    reply = router.handle_event(dm_event("hi"))

    assert reply is not None and "went wrong" in reply.text.lower()
    assert router.conversations["D1"] == []
    assert router.last_error == "boom"


def test_dm_conversations_are_isolated_per_channel(tmp_path: Path) -> None:
    router = make_router(tmp_path, reply="ok")

    router.handle_event(dm_event("hi from c1", channel="D1"))
    router.handle_event(dm_event("hi from c2", channel="D2"))

    assert len(router.conversations["D1"]) == 2
    assert len(router.conversations["D2"]) == 2
    assert router.conversations["D1"][0]["content"] == "hi from c1"
    assert router.conversations["D2"][0]["content"] == "hi from c2"


# -- Channels: ambient chatter is ignored, mentions start a thread ------------


def test_handle_event_ignores_ambient_channel_message_without_thread(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    assert router.handle_event(channel_event("just chatting")) is None


def test_handle_event_ignores_channel_message_in_a_thread_fig_is_not_part_of(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    assert router.handle_event(channel_event("hi", thread_ts="100.1")) is None


def test_app_mention_starts_a_thread_and_replies_in_it(tmp_path: Path) -> None:
    router = make_router(tmp_path, reply="hello from the channel")

    reply = router.handle_app_mention(mention_event("hey Fig", ts="100.1"))

    assert reply is not None
    assert reply.text == "hello from the channel"
    assert reply.thread_ts == "100.1"
    assert ("C1", "100.1") in router.active_threads


def test_app_mention_strips_the_mention_text_before_routing(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    router.handle_app_mention(mention_event("hey Fig", ts="100.1"))

    conversation = router.conversations["C1:100.1"]
    assert conversation[0]["content"] == "hey Fig"


def test_app_mention_from_unauthorized_user_is_ignored(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    event = mention_event("hey Fig", ts="100.1")
    event["user"] = "U_OTHER"

    assert router.handle_app_mention(event) is None
    assert router.active_threads == set()


def test_follow_up_message_in_mentioned_thread_gets_a_reply(tmp_path: Path) -> None:
    router = make_router(tmp_path, reply="first reply")
    router.handle_app_mention(mention_event("hey Fig", ts="100.1"))

    router.session.loop.reply = "second reply"
    reply = router.handle_event(channel_event("follow-up question", thread_ts="100.1"))

    assert reply is not None
    assert reply.text == "second reply"
    assert reply.thread_ts == "100.1"


def test_follow_up_in_mentioned_thread_shares_conversation_with_the_mention(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    router.handle_app_mention(mention_event("hey Fig", ts="100.1"))
    router.handle_event(channel_event("follow-up", thread_ts="100.1"))

    conversation = router.conversations["C1:100.1"]
    assert [m["content"] for m in conversation if m["role"] == "user"] == ["hey Fig", "follow-up"]


def test_app_mention_with_no_text_after_stripping_still_gets_a_reply(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    reply = router.handle_app_mention({"user": "U_OWNER", "text": "<@UBOT123>", "channel": "C1", "ts": "100.1"})
    assert reply is not None


# -- on_start/on_done hooks (e.g. for Slack reactions) ------------------------


def test_dm_fires_on_start_then_on_done_around_the_reply(tmp_path: Path) -> None:
    router = make_router(tmp_path, reply="hello back")
    calls: list[str] = []

    router.handle_event(dm_event("hi"), on_start=lambda: calls.append("start"), on_done=lambda: calls.append("done"))

    assert calls == ["start", "done"]


def test_ignored_dm_message_never_fires_hooks(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    calls: list[str] = []

    router.handle_event(dm_event("   "), on_start=lambda: calls.append("start"), on_done=lambda: calls.append("done"))

    assert calls == []


def test_ignored_unauthorized_dm_never_fires_hooks(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    calls: list[str] = []
    event = dm_event("hi")
    event["user"] = "U_OTHER"

    router.handle_event(event, on_start=lambda: calls.append("start"), on_done=lambda: calls.append("done"))

    assert calls == []


def test_ambient_channel_chatter_never_fires_hooks(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    calls: list[str] = []

    router.handle_event(
        channel_event("just chatting"), on_start=lambda: calls.append("start"), on_done=lambda: calls.append("done")
    )

    assert calls == []


def test_app_mention_fires_on_start_then_on_done(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    calls: list[str] = []

    router.handle_app_mention(
        mention_event("hey Fig", ts="100.1"),
        on_start=lambda: calls.append("start"),
        on_done=lambda: calls.append("done"),
    )

    assert calls == ["start", "done"]


def test_hooks_fire_even_when_agent_loop_errors(tmp_path: Path) -> None:
    router = make_router(tmp_path, raise_error=True)
    calls: list[str] = []

    router.handle_event(dm_event("hi"), on_start=lambda: calls.append("start"), on_done=lambda: calls.append("done"))

    assert calls == ["start", "done"]


# -- conversation_context (so set_reminder can default to "notify here") -----


def test_dm_updates_conversation_context_channel_with_no_thread(tmp_path: Path) -> None:
    router = make_router(tmp_path)

    router.handle_event(dm_event("hi", channel="D1"))

    assert router.session.conversation_context.current_channel == "D1"
    assert router.session.conversation_context.current_thread_ts is None


def test_mentioned_thread_updates_conversation_context_with_thread_ts(tmp_path: Path) -> None:
    router = make_router(tmp_path)

    router.handle_app_mention(mention_event("hey Fig", channel="C1", ts="100.1"))

    assert router.session.conversation_context.current_channel == "C1"
    assert router.session.conversation_context.current_thread_ts == "100.1"


def test_ignored_message_does_not_touch_conversation_context(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    router.session.conversation_context.current_channel = "D_PREVIOUS"

    router.handle_event(channel_event("ambient chatter, not addressed"))

    assert router.session.conversation_context.current_channel == "D_PREVIOUS"


# -- Commands (work the same keyed by any conversation id) --------------------


def test_status_command_reports_configured_model(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    assert "test/model" in router.handle_text("C1", "!status")


def test_restart_command_returns_sentinel_without_executing_anything(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    assert router.handle_text("C1", "!restart") == RESTART_SENTINEL


def test_skills_command_lists_pending_skill(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    router.session.loader.draft_skill("helper", "helps", "do it")

    assert "helper" in router.handle_text("C1", "!skills")


def test_approve_command_activates_pending_skill(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    router.session.loader.draft_skill("helper", "helps", "do it")

    reply = router.handle_text("C1", "!approve helper")

    assert "Approved" in reply
    assert "helper" in [s.name for s in router.session.loader.discover()]


# -- persistence (to_state/restore_state/on_change) ----------------------------


def test_to_state_serializes_active_threads_and_conversations(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    router.handle_app_mention(mention_event("hey Fig", ts="100.1"))

    state = router.to_state()

    assert state["active_threads"] == [["C1", "100.1"]]
    assert state["conversations"]["C1:100.1"][0]["content"] == "hey Fig"


def test_restore_state_repopulates_active_threads_and_conversations(tmp_path: Path) -> None:
    router = make_router(tmp_path)

    router.restore_state(
        {
            "active_threads": [["C1", "100.1"]],
            "conversations": {"C1:100.1": [{"role": "user", "content": "earlier message"}]},
        }
    )

    assert ("C1", "100.1") in router.active_threads
    assert router.conversations["C1:100.1"][0]["content"] == "earlier message"


def test_restored_thread_accepts_a_follow_up_without_re_mentioning(tmp_path: Path) -> None:
    router = make_router(tmp_path, reply="continuing")
    router.restore_state({"active_threads": [["C1", "100.1"]], "conversations": {}})

    reply = router.handle_event(channel_event("follow-up", thread_ts="100.1"))

    assert reply is not None
    assert reply.text == "continuing"


def test_on_change_fires_after_a_dispatched_reply(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    changes: list[str] = []
    router.on_change = lambda: changes.append("changed")

    router.handle_event(dm_event("hi"))

    assert changes == ["changed"]


def test_on_change_does_not_fire_for_an_ignored_message(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    changes: list[str] = []
    router.on_change = lambda: changes.append("changed")

    router.handle_event(channel_event("ambient chatter"))

    assert changes == []


# -- !model command -------------------------------------------------------------


def router_with_models(tmp_path: Path):
    from figai.tools.models import ModelError

    router = make_router(tmp_path)
    log: list[tuple] = []

    class FakeManager:
        def describe(self):
            return "chat: a/chat-1; vision: b/v; email: not set"

        def set(self, role, model):
            if model == "bad":
                raise ModelError("'bad' isn't in OpenRouter's catalog")
            log.append(("set", role, model))
            return f"{role} model is now {model}."

        def reset(self, role):
            log.append(("reset", role))
            return f"{role} model reset."

    router.session.models = FakeManager()
    return router, log


def test_model_command_shows_current_models(tmp_path: Path) -> None:
    router, _ = router_with_models(tmp_path)
    assert "a/chat-1" in router.handle_text("D1", "!model")


def test_model_command_switches_the_chat_model(tmp_path: Path) -> None:
    router, log = router_with_models(tmp_path)
    assert "a/chat-2" in router.handle_text("D1", "!model a/chat-2")
    assert log == [("set", "chat", "a/chat-2")]


def test_model_command_reset_and_error(tmp_path: Path) -> None:
    router, log = router_with_models(tmp_path)
    router.handle_text("D1", "!model reset")
    assert log == [("reset", "chat")]
    assert "catalog" in router.handle_text("D1", "!model bad")


def test_model_command_does_not_reach_the_agent_loop(tmp_path: Path) -> None:
    router, _ = router_with_models(tmp_path)
    router.handle_text("D1", "!model a/chat-2")
    assert router.session.loop.calls == []


def test_health_command_returns_the_report(tmp_path: Path) -> None:
    assert "fine" in make_router(tmp_path).handle_text("C1", "!health")


def test_logs_command_parses_kind_and_count(tmp_path: Path, monkeypatch) -> None:
    seen: list[tuple[str, int]] = []
    monkeypatch.setattr(
        "figai.logs_reader.recent_lines", lambda kind, count, path=None: seen.append((kind, count)) or ["line"]
    )
    router = make_router(tmp_path)
    assert "line" in router.handle_text("C1", "!logs tools 5")
    router.handle_text("C1", "!errors")
    router.handle_text("C1", "!logs")
    assert seen == [("tools", 5), ("errors", 10), ("errors", 10)]


def test_logs_command_when_nothing_matches(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr("figai.logs_reader.recent_lines", lambda kind, count, path=None: [])
    assert "Nothing" in make_router(tmp_path).handle_text("C1", "!logs warnings")


def _briefing_router(tmp_path: Path):
    from figai.briefing.briefing import BriefingManager
    from figai.config import BriefingConfig
    from figai.monitoring.checks import CheckManager

    router = make_router(tmp_path)
    sent: list[str] = []
    router.session.briefing = BriefingManager(
        BriefingConfig(), "U", build=lambda s, n: "B", notify=lambda c, t: sent.append(t)
    )
    router.session.checks = CheckManager(notify=lambda c, t: None, probe=lambda c: (True, "ok"))
    return router, sent


def test_briefing_commands(tmp_path: Path) -> None:
    router, sent = _briefing_router(tmp_path)
    assert "off" in router.handle_text("C1", "!briefing")
    assert "*on*" in router.handle_text("C1", "!briefing on")
    assert "09:00" in router.handle_text("C1", "!briefing time 09:00")
    assert "Email section: off" in router.handle_text("C1", "!briefing email off")
    assert router.handle_text("C1", "!briefing now") == "Sent." and sent == ["B"]
    assert "off" in router.handle_text("C1", "!briefing off")
    assert "Usage" in router.handle_text("C1", "!briefing bogus")
    assert "Time must look like" in router.handle_text("C1", "!briefing time 99:00")


def test_checks_command(tmp_path: Path) -> None:
    router, _ = _briefing_router(tmp_path)
    assert router.handle_text("C1", "!checks") == "No scheduled checks."
    router.session.checks.add("miner", "http://x/", 5, "C1")
    assert "miner" in router.handle_text("C1", "!checks")


def _help_router(tmp_path: Path) -> SlackRouter:
    from figai.skills_system.loader import SkillLoader as RealLoader

    router = make_router(tmp_path)
    router.session.loader = RealLoader(Path(__file__).resolve().parent.parent / "skills")
    return router


def test_help_is_a_menu_with_topics_and_details_on_request(tmp_path: Path) -> None:
    router = _help_router(tmp_path)
    menu = router.handle_text("C1", "!help")
    assert "*Fig help menu*" in menu and "*Commands*" in menu and "`!help <topic>`" in menu
    assert "• *Email (read-only)*" in menu and "summarize my last 5 emails" not in menu.split("*Topics*")[0]
    only_email = router.handle_text("C1", "!help email")
    assert "summarize my last 5 emails" in only_email and "*Reminders*" not in only_email
    assert "*Reminders*" in router.handle_text("C1", "!help all")
    assert "No help matches" in router.handle_text("C1", "!help bogus")


def test_every_bang_command_is_documented_in_the_help_skill(tmp_path: Path) -> None:
    import re

    source = (Path(__file__).resolve().parent.parent / "figai" / "frontends" / "slack_router.py").read_text()
    commands = set(re.findall(r'"(![a-z]+)', source)) - {"!approve"} | {"!approve"}
    sheet = (Path(__file__).resolve().parent.parent / "skills" / "help" / "SKILL.md").read_text()
    missing = sorted(c for c in commands if f"`{c}" not in sheet)
    assert not missing, f"commands missing from skills/help/SKILL.md: {missing}"


def test_help_skill_is_discovered_with_a_description_that_triggers_it() -> None:
    from figai.skills_system.loader import SkillLoader as RealLoader

    skills = {s.name: s.description for s in RealLoader(Path(__file__).resolve().parent.parent / "skills").discover()}
    assert "help" in skills and "what you can do" in skills["help"]


class FakeUpdateTools:
    def __init__(self, resolve_result: dict) -> None:
        self.calls: list[tuple[str, str]] = []
        self.resolve_result = resolve_result

    def execute(self, name: str, arguments: str) -> str:
        import json as _json

        self.calls.append((name, arguments))
        if name == "propose_update_fig":
            return _json.dumps({"status": "awaiting_confirmation", "token": "abc123"})
        return _json.dumps(self.resolve_result)


@pytest.mark.parametrize("text", ["!update", "update yourself -y", "Update yourself --yes", "update fig -y"])
def test_update_shortcut_skips_confirmation(tmp_path: Path, text: str) -> None:
    router = make_router(tmp_path)
    tools = FakeUpdateTools({"status": "updated"})
    router.session.loop.tools = tools
    reply = router.handle_text("C1", text)
    assert "Restarting" in reply
    assert [c[0] for c in tools.calls] == ["propose_update_fig", "resolve_update_fig"]
    assert '"abc123"' in tools.calls[1][1]


def test_update_shortcut_reports_failure(tmp_path: Path) -> None:
    router = make_router(tmp_path)
    router.session.loop.tools = FakeUpdateTools({"error": "Update failed, still running the old code: boom"})
    assert "boom" in router.handle_text("C1", "!update")


def test_plain_update_request_still_goes_to_the_model_and_asks_first(tmp_path: Path) -> None:
    router = make_router(tmp_path, reply="Shall I update?")
    router.session.loop.tools = FakeUpdateTools({"status": "updated"})
    assert router.handle_text("C1", "update yourself") == "Shall I update?"
    assert router.session.loop.tools.calls == []
