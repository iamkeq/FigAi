from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from figai.agent.tools import ToolRegistry
from figai.frontends.voice import build_command_handler
from figai.reminders.context import ConversationContext
from figai.tools.confirm import ConfirmationStore
from figai.tools.slack_post import register_slack_post_tool

CHANNELS = [{"id": "C_GEN", "name": "general"}, {"id": "C_DEV", "name": "dev"}]


def make_tool(confirm: bool = True, sender: bool = True):
    sent: list[tuple[str, str, str | None]] = []
    registry = ToolRegistry()
    register_slack_post_tool(
        registry,
        ConfirmationStore(),
        (lambda ch, text, ts: sent.append((ch, text, ts))) if sender else None,
        lambda: CHANNELS,
        "U_OWNER",
        confirm=confirm,
    )

    def call(name, **args):
        return json.loads(registry.execute(name, json.dumps(args)))

    return call, sent


def test_posting_to_a_channel_asks_first_then_posts() -> None:
    call, sent = make_tool()
    proposed = call("post_slack_message", text="Dinner is at 6", channel="#General")
    assert proposed["status"] == "awaiting_confirmation" and "#General" in proposed["preview"]
    assert sent == []
    assert call("resolve_slack_post", token=proposed["token"]) == {"status": "posted", "to": "#General"}
    assert sent == [("C_GEN", "Dinner is at 6", None)]
    assert "error" in call("resolve_slack_post", token=proposed["token"])  # tokens are single use


def test_posting_to_your_own_dm_needs_no_confirmation() -> None:
    call, sent = make_tool()
    assert call("post_slack_message", text="note to self")["status"] == "posted"
    assert sent == [("U_OWNER", "note to self", None)]


def test_confirmation_can_be_turned_off() -> None:
    call, sent = make_tool(confirm=False)
    assert call("post_slack_message", text="hi", channel="dev")["status"] == "posted"
    assert sent == [("C_DEV", "hi", None)]


@pytest.mark.parametrize(
    "args",
    [{"text": "x", "channel": "nope"}, {"text": "   "}, {"text": "y" * 3001}],
)
def test_bad_posts_are_refused(args) -> None:
    call, sent = make_tool()
    assert "error" in call("post_slack_message", **args)
    assert sent == []


def test_no_slack_connection_is_a_clean_error() -> None:
    call, _ = make_tool(sender=False)
    assert "isn't connected" in call("post_slack_message", text="hi")["error"]


# -- voice replies --------------------------------------------------------------


def make_voice(reply_to: str = "slack", sender_works: bool = True, reply: str = "Done."):
    sent: list[tuple[str, str]] = []
    notified: list[str] = []
    seen_channel: list[str | None] = []

    class Loop:
        def run(self, conversation):
            seen_channel.append(session.conversation_context.current_channel)
            return reply

    def message_sender(ch, text, ts):
        if not sender_works:
            raise RuntimeError("slack down")
        sent.append((ch, text))

    session = SimpleNamespace(
        loop=Loop(),
        conversation_context=ConversationContext(),
        message_sender=message_sender,
        run_lock=threading.RLock(),
    )
    config = SimpleNamespace(
        settings=SimpleNamespace(allowed_slack_user_id="U_OWNER"),
        app=SimpleNamespace(voice=SimpleNamespace(reply_to=reply_to)),
    )
    handler = build_command_handler(config, session, notifier=lambda title, msg: notified.append(msg))
    return handler, sent, notified, seen_channel


def test_voice_reply_goes_to_the_slack_dm_not_a_popup() -> None:
    handler, sent, notified, seen = make_voice()
    handler("post in general that dinner is at six")
    assert sent == [("U_OWNER", ":microphone: _post in general that dinner is at six_\nDone.")]
    assert notified == []
    assert seen == ["U_OWNER"]  # "here" means your DM, so reminders etc. have somewhere to go


def test_voice_reply_modes() -> None:
    handler, sent, notified, _ = make_voice(reply_to="both")
    handler("hello")
    assert len(sent) == 1 and notified == ["Done."]
    handler, sent, notified, _ = make_voice(reply_to="notification")
    handler("hello")
    assert sent == [] and notified == ["Done."]


def test_voice_falls_back_to_a_notification_when_slack_fails() -> None:
    handler, sent, notified, _ = make_voice(sender_works=False)
    handler("hello")
    assert sent == [] and notified == ["Done."]


def test_voice_agent_failure_is_swallowed_and_not_remembered() -> None:
    sent: list[tuple[str, str]] = []

    class Boom:
        def run(self, conversation):
            raise RuntimeError("model down")

    session = SimpleNamespace(
        loop=Boom(),
        conversation_context=ConversationContext(),
        message_sender=lambda ch, text, ts: sent.append((ch, text)),
        run_lock=threading.RLock(),
    )
    config = SimpleNamespace(
        settings=SimpleNamespace(allowed_slack_user_id="U_OWNER"),
        app=SimpleNamespace(voice=SimpleNamespace(reply_to="slack")),
    )
    handler = build_command_handler(config, session, notifier=lambda *_a: None)
    handler("hello")  # must not raise
    assert sent == []
