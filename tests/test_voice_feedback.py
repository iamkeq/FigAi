from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from figai.agent.auto_confirm import ALL_RESOLVERS
from figai.agent.tools import Tool, ToolRegistry
from figai.frontends.voice import build_command_handler
from figai.reminders.context import ConversationContext
from figai.tools.confirm import ConfirmationStore
from figai.voice.outcome import classify
from figai.voice.shortcuts import is_no_phrase, is_yes_phrase
from figai.voice.speech import Speaker, phrases


# -- speaker ------------------------------------------------------------------------------------


class Audio:
    def __init__(self) -> None:
        self.synthesized: list[str] = []
        self.played: list[str] = []

    def synth(self, text, path, voice, rate) -> bool:
        self.synthesized.append(text)
        path.write_bytes(b"RIFF")
        return True

    def play(self, path: Path) -> None:
        self.played.append(path.name)


def make_speaker(tmp_path: Path, honorific: str = "sir", **kwargs):
    audio = Audio()
    speaker = Speaker(tmp_path / "cache", honorific=honorific, synth=audio.synth, player=audio.play, background=False, **kwargs)
    return speaker, audio


def test_phrases_use_the_honorific_or_drop_it() -> None:
    assert phrases("sir")["ack"] == "Yes, sir." and phrases("sir")["done"] == "All set, sir."
    assert phrases("ma'am")["confirm"] == "Shall I proceed, ma'am?"
    assert phrases("")["ack"] == "Yes." and phrases("  ")["done"] == "All set."


def test_prepare_renders_each_phrase_once_and_play_uses_the_cache(tmp_path: Path) -> None:
    speaker, audio = make_speaker(tmp_path)
    speaker.prepare()
    assert sorted(audio.synthesized) == sorted(phrases("sir").values())
    speaker.prepare()  # cached now
    assert len(audio.synthesized) == len(phrases("sir"))
    speaker.play("done")
    speaker.play("nonsense")  # unknown keys are ignored
    assert len(audio.played) == 1


def test_play_before_prepare_is_silent_not_an_error(tmp_path: Path) -> None:
    speaker, audio = make_speaker(tmp_path)
    speaker.play("ack")
    assert audio.played == []


def test_say_and_say_then_synthesize_on_demand_and_cache(tmp_path: Path) -> None:
    speaker, audio = make_speaker(tmp_path)
    speaker.say_then("Post to general: hello", "confirm")
    assert audio.synthesized == ["Post to general: hello Shall I proceed, sir?"]
    speaker.say_then("Post to general: hello", "confirm")
    assert len(audio.synthesized) == 1 and len(audio.played) == 2
    speaker.say("   ")
    assert len(audio.played) == 2


def test_a_failing_synth_or_player_never_breaks_the_caller(tmp_path: Path) -> None:
    def broken_player(path):
        raise OSError("no sound device")

    speaker = Speaker(tmp_path, synth=lambda *a: False, player=broken_player, background=False)
    speaker.say("hello")
    speaker.prepare()
    speaker2 = Speaker(tmp_path / "x", synth=lambda t, p, v, r: p.write_bytes(b"x") or True, player=broken_player, background=False)
    speaker2.prepare()
    speaker2.play("ack")  # must not raise


def test_background_playback_keeps_phrases_in_order(tmp_path: Path) -> None:
    played: list[str] = []
    done = threading.Event()

    def slow_player(path):
        time.sleep(0.05 if not played else 0)
        played.append(path.name)
        if len(played) == 2:
            done.set()

    speaker = Speaker(tmp_path, synth=lambda t, p, v, r: p.write_bytes(b"x") or True, player=slow_player)
    speaker.prepare()
    speaker.play("ack")
    speaker.play("done")
    assert done.wait(2)
    assert played == [speaker._path("Yes, sir.").name, speaker._path("All set, sir.").name]


# -- classifying a command's result ----------------------------------------------------------------


def results(*pairs):
    return [(name, json.dumps(data)) for name, data in pairs]


def test_classify() -> None:
    assert classify([]) == ("answer", None)
    assert classify(results(("list_open_tabs", {"tabs": []}))) == ("answer", None)  # a question, not an action
    assert classify(results(("post_slack_message", {"status": "posted"}))) == ("done", None)
    assert classify(results(("focus_window", {"status": "focused"}), ("list_windows", {"windows": []}))) == ("done", None)
    assert classify(results(("open_program", {"error": "can't find"}))) == ("fail", None)
    assert classify(results(("post_slack_message", {"auto_confirmed": "x", "result": {"error": "no channel"}}))) == ("fail", None)
    assert classify(results(("post_slack_message", {"auto_confirmed": "x", "result": {"status": "posted"}}))) == ("done", None)
    assert classify(results(("propose_ssh_command", {"status": "awaiting_confirmation", "preview": "Run ls"}))) == ("confirm", "Run ls")
    assert classify([("x", "not json")]) == ("answer", None)


def test_registry_observe_sees_every_tool_run_inside_the_block() -> None:
    registry = ToolRegistry()
    registry.register(Tool(name="t", description="d", parameters={"type": "object", "properties": {}}, handler=lambda a: '{"ok": 1}'))
    registry.execute("t", "{}")
    with registry.observe() as seen:
        registry.execute("t", "{}")
        registry.execute("nope", "{}")
    registry.execute("t", "{}")
    assert [n for n, _ in seen] == ["t", "nope"] and registry._observers == []


def test_confirmation_store_latest_and_cancel() -> None:
    store = ConfirmationStore(timeout_seconds=300)
    assert store.latest() is None
    first = store.propose("slack_post", {}, "Post one")
    second = store.propose("memory_delete", {}, "Delete two")
    assert store.latest() == (second, "memory_delete", "Delete two")
    assert store.cancel(second) and not store.cancel(second)
    assert store.latest() == (first, "slack_post", "Post one")
    expired = ConfirmationStore(timeout_seconds=-1)
    expired.propose("slack_post", {}, "old")
    assert expired.latest() is None


@pytest.mark.parametrize("phrase", ["yes", "Yes, sir", "yeah", "go ahead", "hey, do it", "Confirm."])
def test_yes_phrases(phrase: str) -> None:
    assert is_yes_phrase(phrase) and not is_no_phrase(phrase)


@pytest.mark.parametrize("phrase", ["no", "Cancel that", "never mind", "no sir", "hey fig, stop"])
def test_no_phrases(phrase: str) -> None:
    assert is_no_phrase(phrase) and not is_yes_phrase(phrase)


def test_every_confirmable_action_can_be_resolved_by_voice() -> None:
    for action in ("slack_post", "send_file", "update_fig", "add_media", "memory_move", "agent_restart", "memory_delete",
                   "memory_overwrite", "ssh_run", "config_change", "cron_change", "force_close"):
        assert action in ALL_RESOLVERS


# -- the voice handler -----------------------------------------------------------------------------


class SoundLog:
    def __init__(self) -> None:
        self.events: list[str] = []

    def play(self, key: str) -> None:
        self.events.append(key)

    def say_then(self, text: str, key: str) -> None:
        self.events.append(f"say:{text}|{key}")


def make_voice(tool_results, reply: str = "model says hi", model_tools: list[tuple[str, dict]] | None = None):
    store = ConfirmationStore()
    sounds = SoundLog()
    calls: list[tuple[str, dict]] = []
    dms: list[str] = []

    class Tools:
        def __init__(self) -> None:
            self.registry = ToolRegistry()

        def execute(self, name, arguments):
            args = json.loads(arguments)
            calls.append((name, args))
            return json.dumps(tool_results(name, args, store))

        def observe(self):
            return self.registry.observe()

    tools = Tools()

    class Loop:
        def __init__(self) -> None:
            self.tools = tools

        def run(self, conversation):
            for name, args in model_tools or []:
                result = json.dumps(tool_results(name, args, store))
                for seen in tools.registry._observers:
                    seen.append((name, result))
            return reply

    session = SimpleNamespace(
        loop=Loop(), conversation_context=ConversationContext(), message_sender=lambda ch, t, ts: dms.append(t),
        run_lock=threading.RLock(), channel_lister=lambda: [{"id": "C1", "name": "general"}], confirmations=store,
    )
    config = SimpleNamespace(
        settings=SimpleNamespace(allowed_slack_user_id="U"),
        app=SimpleNamespace(
            voice=SimpleNamespace(reply_to="slack"),
            conversation=SimpleNamespace(max_messages=30, voice_idle_minutes=10, dm_idle_minutes=120),
        ),
    )
    handler = build_command_handler(config, session, notifier=lambda *_a: None, speaker=sounds)
    return handler, sounds, calls, dms, store


def test_an_action_says_yes_sir_then_all_set_sir() -> None:
    handler, sounds, calls, _, _ = make_voice(lambda n, a, s: {"status": "focused", "app": "Discord"})
    handler("bring discord up")
    assert sounds.events == ["ack", "done"]


def test_a_failed_action_says_ack_then_fail() -> None:
    handler, sounds, *_ = make_voice(lambda n, a, s: {"error": "There's no open window"})
    handler("minimize discord")
    assert sounds.events == ["ack", "fail"]


def test_a_question_says_only_yes_sir_and_the_answer_goes_to_dm() -> None:
    handler, sounds, _, dms, _ = make_voice(
        lambda n, a, s: {"tabs": []}, model_tools=[("list_open_tabs", {})], reply="You have 3 tabs."
    )
    handler("how many tabs do I have open")
    assert sounds.events == ["ack"] and dms[-1].endswith("You have 3 tabs.")


def test_the_model_path_says_all_set_after_an_action() -> None:
    handler, sounds, *_ = make_voice(lambda n, a, s: {"status": "posted"}, model_tools=[("post_slack_message", {})])
    handler("do something complicated for me")
    assert sounds.events == ["ack", "done"]


def test_a_confirmation_is_read_aloud_then_yes_carries_it_out() -> None:
    def results(name, args, store):
        if name == "post_slack_message":
            token = store.propose("slack_post", {}, "Post to #general: hello")
            return {"status": "awaiting_confirmation", "token": token, "preview": "Post to #general: hello"}
        if name == "resolve_slack_post":
            store.resolve(args["token"], "slack_post")
            return {"status": "posted", "to": "#general"}
        return {}

    handler, sounds, calls, dms, store = make_voice(results)
    handler("type in general hello")
    assert sounds.events == ["ack", "say:Post to #general: hello|confirm"]
    assert store.latest() is not None
    handler("yes sir")
    assert sounds.events[-1] == "done" and calls[-1][0] == "resolve_slack_post"
    assert dms[-1].endswith("Done: Post to #general: hello") and store.latest() is None


def test_no_cancels_the_pending_action_and_says_very_well() -> None:
    def results(name, args, store):
        token = store.propose("memory_delete", {}, "Delete the note 'todo'")
        return {"status": "awaiting_confirmation", "token": token, "preview": "Delete the note 'todo'"}

    handler, sounds, calls, dms, store = make_voice(results, model_tools=[("memory_delete", {"path": "todo.md"})])
    handler("delete my todo note")
    assert store.latest() is not None and sounds.events[-1].startswith("say:Delete the note")
    handler("no, cancel that")
    assert store.latest() is None and sounds.events[-1] == "cancelled" and dms[-1].endswith("Cancelled: Delete the note 'todo'")
    assert not any(n.startswith("resolve_") for n, _ in calls)


def test_yes_with_nothing_pending_is_just_a_normal_command() -> None:
    handler, sounds, calls, _, _ = make_voice(lambda n, a, s: {}, reply="Yes what?")
    handler("yes")
    assert not any(n.startswith("resolve_") for n, _ in calls) and sounds.events[0] == "ack"


def test_without_a_speaker_nothing_changes() -> None:
    store = ConfirmationStore()
    session = SimpleNamespace(
        loop=SimpleNamespace(run=lambda c: "hi"), conversation_context=ConversationContext(),
        message_sender=lambda *a: None, run_lock=threading.RLock(), confirmations=store,
    )
    config = SimpleNamespace(
        settings=SimpleNamespace(allowed_slack_user_id="U"),
        app=SimpleNamespace(voice=SimpleNamespace(reply_to="slack"), conversation=SimpleNamespace(max_messages=30, voice_idle_minutes=10, dm_idle_minutes=120)),
    )
    build_command_handler(config, session, notifier=lambda *_a: None)("hello")  # no speaker: must not raise
