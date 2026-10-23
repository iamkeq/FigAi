from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from figai.frontends.voice import build_command_handler
from figai.reminders.context import ConversationContext
from figai.tools.programs import match_name
from figai.voice.shortcuts import match_open_app


def test_notepad_is_not_confused_with_notepad_plus_plus() -> None:
    assert match_name("notepad", ["Notepad", "Notepad++", "Spotify"]) == ("Notepad", ["Notepad"])
    assert match_name("Notepad++", ["Notepad", "Notepad++"])[0] == "Notepad++"
    best, candidates = match_name("note", ["Notepad", "Notepad++"])  # genuinely ambiguous
    assert best is None and candidates == ["Notepad", "Notepad++"]


@pytest.mark.parametrize(
    "spoken,app",
    [("open notepad", "notepad"), ("Open up Spotify.", "spotify"), ("can you launch the calculator", "calculator"),
     ("please open steam for me", "steam"), ("open visual studio code", "visual studio code"), ("open obs", "obs")],
)
def test_open_phrases(spoken: str, app: str) -> None:
    assert match_open_app(spoken) == app


@pytest.mark.parametrize(
    "spoken",
    ["open youtube.com", "open a new tab", "open the file", "open my downloads folder", "open https://example.com",
     "open the door", "open me up", "open", "open my email", "open a link to the thing in the other place now"],
)
def test_websites_tabs_files_and_chit_chat_go_to_the_model(spoken: str) -> None:
    assert match_open_app(spoken) is None


def make_voice(response):
    calls: list[tuple[str, dict]] = []
    dms: list[str] = []
    model_used: list[int] = []

    class Tools:
        def execute(self, name, arguments):
            calls.append((name, json.loads(arguments)))
            return json.dumps(response)

    class Loop:
        tools = Tools()

        def run(self, conversation):
            model_used.append(1)
            return "model answer"

    session = SimpleNamespace(
        loop=Loop(), conversation_context=ConversationContext(),
        message_sender=lambda ch, text, ts: dms.append(text), run_lock=threading.RLock(), channel_lister=None,
    )
    config = SimpleNamespace(
        settings=SimpleNamespace(allowed_slack_user_id="U"),
        app=SimpleNamespace(
            voice=SimpleNamespace(reply_to="slack"),
            conversation=SimpleNamespace(max_messages=30, voice_idle_minutes=10, dm_idle_minutes=120),
        ),
    )
    return build_command_handler(config, session, notifier=lambda *_a: None), calls, dms, model_used


def test_voice_open_launches_the_app_without_the_model() -> None:
    handler, calls, dms, model_used = make_voice({"status": "launched", "program": "Notepad"})
    handler("open notepad")
    assert calls == [("open_program", {"name": "notepad"})] and dms[0].endswith("Opened Notepad.") and model_used == []


def test_voice_open_defers_unknown_names_to_the_model() -> None:
    handler, calls, dms, model_used = make_voice({"error": "I can't find an app called 'bananas'."})
    handler("open bananas")
    assert calls == [("open_program", {"name": "bananas"})]
    assert model_used == [1] and dms[0].endswith("model answer")  # the model got its chance (e.g. it's a website)


def test_voice_open_asks_which_when_ambiguous() -> None:
    handler, _, dms, model_used = make_voice({"error": "which one?", "candidates": ["Notepad", "Notepad++"]})
    handler("open note")
    assert "Notepad, Notepad++" in dms[0] and model_used == []
