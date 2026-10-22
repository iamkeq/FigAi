from __future__ import annotations

import json
import threading
from pathlib import Path
from types import SimpleNamespace

from figai.agent.history import is_idle, trim_history
from figai.frontends.slack_router import SlackRouter
from figai.frontends.voice import build_command_handler
from figai.reminders.context import ConversationContext
from figai.voice.shortcuts import is_reset_phrase


def convo(n: int) -> list[dict]:
    return [{"role": "user" if i % 2 == 0 else "assistant", "content": str(i)} for i in range(n)]


def test_trim_keeps_the_newest_and_starts_on_a_user_message() -> None:
    messages = convo(10)
    assert trim_history(messages, 5) == 5 or messages[0]["role"] == "user"
    assert messages[0]["role"] == "user" and messages[-1]["content"] == "9" and len(messages) <= 5


def test_trim_is_a_noop_when_short_or_disabled() -> None:
    short, disabled = convo(4), convo(50)
    assert trim_history(short, 30) == 0 and len(short) == 4
    assert trim_history(disabled, 0) == 0 and len(disabled) == 50


def test_is_idle() -> None:
    assert not is_idle(None, 1000, 10)
    assert not is_idle(1000, 1000 + 599, 10)
    assert is_idle(1000, 1000 + 601, 10)
    assert not is_idle(0.0001, 10**9, 0)  # 0 = never expires


def test_reset_phrases() -> None:
    for phrase in ["Start over.", "New session", "hey, forget everything", "Please reset the conversation", "fresh start!"]:
        assert is_reset_phrase(phrase), phrase
    for phrase in ["start the timer", "reset my router", "what is a new session", "open spotify"]:
        assert not is_reset_phrase(phrase), phrase


def make_voice(now: dict, max_messages: int = 30, idle: float = 10):
    seen: list[list[str]] = []
    dms: list[str] = []

    class Loop:
        tools = SimpleNamespace(execute=lambda *a: "{}")

        def run(self, conversation):
            seen.append([m["content"] for m in conversation])
            return f"reply{len(seen)}"

    session = SimpleNamespace(
        loop=Loop(),
        conversation_context=ConversationContext(),
        message_sender=lambda ch, text, ts: dms.append(text),
        run_lock=threading.RLock(),
    )
    config = SimpleNamespace(
        settings=SimpleNamespace(allowed_slack_user_id="U"),
        app=SimpleNamespace(
            voice=SimpleNamespace(reply_to="slack"),
            conversation=SimpleNamespace(max_messages=max_messages, voice_idle_minutes=idle, dm_idle_minutes=120),
        ),
    )
    return build_command_handler(config, session, notifier=lambda *_a: None, clock=lambda: now["t"]), seen, dms


def test_voice_session_remembers_within_the_window_and_forgets_after_idle() -> None:
    now = {"t": 1000.0}
    handler, seen, _ = make_voice(now)
    handler("what time is it")
    now["t"] += 60
    handler("and in tokyo")
    assert seen[1] == ["what time is it", "reply1", "and in tokyo"]
    now["t"] += 11 * 60  # quiet for 11 minutes
    handler("something new")
    assert seen[2] == ["something new"]


def test_voice_history_is_capped() -> None:
    now = {"t": 0.0}
    handler, seen, _ = make_voice(now, max_messages=6)
    for i in range(10):
        now["t"] += 10
        handler(f"question {i}")
    assert len(seen[-1]) <= 6 and seen[-1][-1] == "question 9"


def test_saying_start_over_clears_the_voice_session() -> None:
    now = {"t": 0.0}
    handler, seen, dms = make_voice(now)
    handler("remember the number seven")
    handler("start over")
    handler("what number")
    assert seen[-1] == ["what number"]
    assert any("Fresh start" in d for d in dms)


# -- Slack ----------------------------------------------------------------------------


def make_router(tmp_path: Path, now: dict, dm_idle: float = 120):
    from tests.test_slack_router import make_app_config

    seen: list[list[str]] = []

    class Loop:
        def run(self, conversation):
            seen.append([m["content"] for m in conversation])
            return "ok"

    config = make_app_config(tmp_path)
    app = config.app
    app.conversation.dm_idle_minutes = dm_idle
    app.conversation.max_messages = 6
    session = SimpleNamespace(loop=Loop(), loader=None, conversation_context=ConversationContext())
    router = SlackRouter(
        session=session, config=config, allowed_user_id="U", clock=lambda: now["t"]
    )
    return router, seen


def test_dm_starts_fresh_after_idle_but_threads_do_not(tmp_path: Path) -> None:
    now = {"t": 1000.0}
    router, seen = make_router(tmp_path, now)
    router.handle_text("D1", "first")
    router.handle_text("C1:100.1", "thread first")
    now["t"] += 3 * 3600
    router.handle_text("D1", "second")
    router.handle_text("C1:100.1", "thread second")
    assert seen[2] == ["second"]  # DM forgot
    assert seen[3] == ["thread first", "ok", "thread second"]  # thread remembered


def test_new_command_and_history_cap(tmp_path: Path) -> None:
    now = {"t": 0.0}
    router, seen = make_router(tmp_path, now)
    for i in range(8):
        now["t"] += 1
        router.handle_text("D1", f"m{i}")
    assert len(seen[-1]) <= 6
    assert "Fresh start" in router.handle_text("D1", "!new")
    router.handle_text("D1", "after reset")
    assert seen[-1] == ["after reset"]


def test_last_active_survives_a_restart(tmp_path: Path) -> None:
    now = {"t": 5.0}
    router, _ = make_router(tmp_path, now)
    router.handle_text("D1", "hi")
    state = router.to_state()
    router2, _ = make_router(tmp_path, now)
    router2.restore_state(json.loads(json.dumps(state)))
    assert router2.last_active == {"D1": 5.0}
