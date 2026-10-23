from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from figai.agent.tools import Tool, ToolRegistry
from figai.reminders.context import ConversationContext
from figai.tools.window_focus import WindowInfo, pick_window, register_window_focus_tools
from figai.voice.shortcuts import match_focus_window

WINDOWS = [  # front-to-back, like EnumWindows
    WindowInfo(1, "#general | Dave's server - Discord", "Discord.exe", minimized=True),
    WindowInfo(2, "FigAI - Google Chrome", "chrome.exe"),
    WindowInfo(3, "main.py - figai - Visual Studio Code", "Code.exe"),
    WindowInfo(4, "Spotify Premium", "Spotify.exe"),
    WindowInfo(5, "Inbox - Mail", "chrome.exe"),
    WindowInfo(6, "notes.txt - Notepad", "notepad.exe"),
    WindowInfo(7, "Notepad++ - todo", "notepad++.exe"),
]


@pytest.mark.parametrize(
    "spoken,hwnd",
    [("discord", 1), ("Discord", 1), ("chrome", 2), ("google chrome", 2), ("vscode", 3),
     ("visual studio code", 3), ("vs code", 3), ("code", 3), ("spotify", 4), ("spot", 4)],
)
def test_pick_window_by_app_name(spoken: str, hwnd: int | None) -> None:
    window, candidates = pick_window(spoken, WINDOWS)
    assert window is not None and window.hwnd == hwnd and candidates == []


def test_an_app_with_several_windows_picks_the_most_recent_one() -> None:
    assert pick_window("chrome", WINDOWS)[0].hwnd == 2  # not the later "Inbox - Mail" chrome window


def test_matches_on_window_title_when_the_process_name_differs() -> None:
    assert pick_window("inbox", WINDOWS)[0].hwnd == 5


def test_different_apps_matching_equally_ask_which() -> None:
    window, candidates = pick_window("notepad", WINDOWS)
    assert window is None and candidates == ["notepad", "notepad++"]
    both = [WindowInfo(1, "a", "alpha1.exe"), WindowInfo(2, "b", "alpha2.exe")]
    window, candidates = pick_window("alpha", both)
    assert window is None and candidates == ["alpha1", "alpha2"]


def test_unknown_or_tiny_names_match_nothing() -> None:
    assert pick_window("photoshop", WINDOWS) == (None, [])
    assert pick_window("x", WINDOWS) == (None, [])


def make_tools(windows=WINDOWS, focus_ok: bool = True, with_open: bool = False, open_ok: bool = True):
    focused: list[int] = []
    registry = ToolRegistry()
    if with_open:
        registry.register(
            Tool(
                name="open_program", description="d", parameters={"type": "object", "properties": {}},
                handler=lambda a: json.dumps({"status": "launched", "program": a["name"].title()} if open_ok else {"error": "not installed"}),
            )
        )
    register_window_focus_tools(registry, lister=lambda: list(windows), focuser=lambda h: focused.append(h) or focus_ok)

    def call(tool, **args):
        return json.loads(registry.execute(tool, json.dumps(args)))

    return call, focused


def test_focus_window_brings_up_a_minimized_app() -> None:
    call, focused = make_tools()
    assert call("focus_window", name="discord") == {
        "status": "focused", "app": "Discord", "window": "#general | Dave's server - Discord", "was_minimized": True,
    }
    assert focused == [1]


def test_focus_that_windows_blocks_is_reported_honestly() -> None:
    call, _ = make_tools(focus_ok=False)
    out = call("focus_window", name="spotify")
    assert out["status"] == "asked" and "behind" in out["note"]


def test_a_closed_app_is_launched_instead() -> None:
    call, focused = make_tools(with_open=True)
    out = call("focus_window", name="steam")
    assert out["status"] == "launched" and out["program"] == "Steam" and focused == []


def test_a_closed_app_that_cannot_be_launched_says_so() -> None:
    call, _ = make_tools(with_open=True, open_ok=False)
    assert "couldn't start it" in call("focus_window", name="steam")["error"]
    call2, _ = make_tools()
    assert "no open window" in call2("focus_window", name="steam")["error"]


def test_ambiguous_names_ask_and_list_windows() -> None:
    call, focused = make_tools([WindowInfo(1, "a", "alpha1.exe"), WindowInfo(2, "b", "alpha2.exe")])
    assert call("focus_window", name="alpha")["candidates"] == ["alpha1", "alpha2"] and focused == []
    listing = call("list_windows")["windows"]
    assert listing[0] == {"app": "alpha1", "title": "a", "minimized": False}


def test_unsupported_platform_is_a_clean_error(monkeypatch) -> None:
    import sys

    monkeypatch.setattr(sys, "platform", "linux")
    registry = ToolRegistry()
    register_window_focus_tools(registry)
    assert "not supported" in json.loads(registry.execute("focus_window", json.dumps({"name": "discord"})))["error"]


@pytest.mark.parametrize(
    "spoken,app",
    [
        ("Bring discord up", "discord"),
        ("can you bring discord up", "discord"),
        ("Bring up Discord.", "discord"),
        ("pull up spotify", "spotify"),
        ("switch to chrome", "chrome"),
        ("please focus on visual studio code", "visual studio code"),
        ("bring chrome to the front", "chrome"),
        ("hey, bring up the calculator window", "calculator"),
    ],
)
def test_focus_phrases(spoken: str, app: str) -> None:
    assert match_focus_window(spoken) == app


@pytest.mark.parametrize(
    "spoken", ["bring me a coffee", "what time is it", "open spotify", "show me my tabs", "switch to", "go to discord", "type in general hello"]
)
def test_other_speech_is_not_a_focus_request(spoken: str) -> None:
    assert match_focus_window(spoken) is None


def test_voice_focus_request_skips_the_model_and_replies_in_dm() -> None:
    from figai.frontends.voice import build_command_handler

    calls: list[tuple[str, dict]] = []
    dms: list[str] = []
    model_used: list[int] = []

    class Tools:
        def execute(self, name, arguments):
            calls.append((name, json.loads(arguments)))
            return json.dumps({"status": "focused", "app": "Discord", "window": "w", "was_minimized": True})

    class Loop:
        tools = Tools()

        def run(self, conversation):
            model_used.append(1)
            return "no"

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
    handler = build_command_handler(config, session, notifier=lambda *_a: None)
    handler("can you bring discord up")
    assert calls == [("focus_window", {"name": "discord"})] and model_used == []
    assert dms == [":microphone: _can you bring discord up_\nBrought up Discord."]


# -- minimize / maximize / close -----------------------------------------------------------------

from figai.voice.shortcuts import WindowAction, match_window_action  # noqa: E402


def make_state_tools(windows=WINDOWS):
    changes: list[tuple[int, str]] = []
    all_minimized: list[int] = []
    registry = ToolRegistry()
    register_window_focus_tools(
        registry,
        lister=lambda: list(windows),
        focuser=lambda h: True,
        state_setter=lambda h, s: changes.append((h, s)),
        minimize_all=lambda: all_minimized.append(1),
    )

    def call(tool, **args):
        return json.loads(registry.execute(tool, json.dumps(args)))

    return call, changes, all_minimized


def test_minimize_and_maximize_by_name() -> None:
    call, changes, _ = make_state_tools()
    assert call("minimize_window", name="discord") == {"status": "minimized", "app": "Discord", "window": "#general | Dave's server - Discord"}
    assert call("maximize_window", name="spotify")["status"] == "maximized"
    assert changes == [(1, "minimize"), (4, "maximize")]


def test_minimize_the_current_window_and_everything() -> None:
    call, changes, all_minimized = make_state_tools()
    assert call("minimize_window", name="this")["app"] == "Discord"  # front-most window
    assert call("minimize_window")["status"] == "minimized"  # no name = current
    assert changes == [(1, "minimize"), (1, "minimize")]
    assert call("minimize_window", name="everything") == {"status": "minimized", "app": "all windows"}
    assert all_minimized == [1]
    assert "error" in call("maximize_window", name="photoshop")


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("minimize discord", WindowAction("minimize", "discord")),
        ("Minimise the chrome window", WindowAction("minimize", "chrome")),
        ("can you minimize this", WindowAction("minimize", "current")),
        ("minimize the current window.", WindowAction("minimize", "current")),
        ("Minimize everything", WindowAction("minimize", "all")),
        ("maximize visual studio code", WindowAction("maximize", "visual studio code")),
        ("close spotify", WindowAction("close", "spotify")),
        ("please quit discord", WindowAction("close", "discord")),
        ("close the active window", WindowAction("close", "current")),
        ("exit steam app", WindowAction("close", "steam")),
    ],
)
def test_window_action_phrases(spoken: str, expected) -> None:
    assert match_window_action(spoken) == expected


@pytest.mark.parametrize(
    "spoken", ["close everything", "close all windows", "close it", "close me", "what is the time", "minimize", "close the door and open spotify please now ok"]
)
def test_dangerous_or_unclear_close_phrases_are_not_matched(spoken: str) -> None:
    result = match_window_action(spoken)
    assert result is None or (result.action == "close" and result.target not in ("all", "everything"))
    assert match_window_action("close everything") is None and match_window_action("close all windows") is None


def make_voice_session(responses):
    from figai.frontends.voice import build_command_handler

    calls: list[tuple[str, dict]] = []
    dms: list[str] = []

    class Tools:
        def execute(self, name, arguments):
            calls.append((name, json.loads(arguments)))
            return json.dumps(responses(name, json.loads(arguments)))

    class Loop:
        tools = Tools()

        def run(self, conversation):
            raise AssertionError("the model should not be used")

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
    return build_command_handler(config, session, notifier=lambda *_a: None), calls, dms


def test_voice_minimize_calls_the_tool_and_replies() -> None:
    handler, calls, dms = make_voice_session(lambda n, a: {"status": "minimized", "app": "Discord"})
    handler("minimize discord")
    assert calls == [("minimize_window", {"name": "discord"})]
    assert dms[0].endswith("Minimized Discord.")
    handler("minimize this")
    assert calls[1] == ("minimize_window", {"name": ""})


def test_voice_close_uses_close_program_and_reports_stubborn_apps() -> None:
    handler, calls, dms = make_voice_session(lambda n, a: {"status": "closed", "program": "Spotify.exe"})
    handler("close spotify")
    assert calls == [("close_program", {"name": "spotify"})] and dms[0].endswith("Closed Spotify.exe.")
    handler2, _, dms2 = make_voice_session(lambda n, a: {"status": "asked_to_close", "program": "word.exe"})
    handler2("quit word")
    assert "still open" in dms2[0] and "save prompt" in dms2[0]
    handler3, _, dms3 = make_voice_session(lambda n, a: {"error": "explorer.exe is a protected system process"})
    handler3("close explorer")
    assert "couldn't close" in dms3[0] and "protected" in dms3[0]


def test_voice_close_this_closes_the_front_app_via_the_window_list() -> None:
    def responses(name, args):
        if name == "list_windows":
            return {"windows": [{"app": "notepad", "title": "x", "minimized": False}]}
        return {"status": "closed", "program": "notepad.exe"}

    handler, calls, dms = make_voice_session(responses)
    handler("close this")
    assert [c[0] for c in calls] == ["list_windows", "close_program"] and calls[1][1] == {"name": "notepad"}


def test_voice_never_closes_everything() -> None:
    handler, calls, _ = make_voice_session(lambda n, a: {})
    handler("close everything")  # not a shortcut: it goes to the model path (which this fake rejects), never to close_program
    handler("close all windows")
    assert calls == []
