from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from figai.config import AppConfig
from figai.frontends.slack_router import (
    RESTART_SENTINEL,
    SlackRouter,
    is_authorized_message,
    strip_mentions,
)
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
    session = SimpleNamespace(loop=FakeLoop(reply, raise_error), loader=loader)
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
