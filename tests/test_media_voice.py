from __future__ import annotations

import json
import threading
from types import SimpleNamespace

import pytest

from figai.frontends.slack_router import SlackRouter
from figai.frontends.voice import build_command_handler
from figai.reminders.context import ConversationContext
from figai.voice.shortcuts import MediaAdd, match_media_add, match_media_query


@pytest.mark.parametrize(
    "spoken,kind",
    [
        ("what's downloading", "current"),
        ("What is currently downloading?", "current"),
        ("what's being downloaded right now", "current"),
        ("what's in the download queue", "current"),
        ("show me the downloads", "current"),
        ("check sab", "current"),
        ("what's on the queue", "current"),
        ("what was recently downloaded", "recent"),
        ("what finished downloading", "recent"),
        ("what did you download today", "recent"),
        ("show me what downloaded lately", "recent"),
        ("what has been downloaded", "recent"),
    ],
)
def test_media_questions(spoken: str, kind: str) -> None:
    assert match_media_query(spoken) == kind


@pytest.mark.parametrize(
    "spoken", ["download dune", "what time is it", "how many tabs do I have open", "turn the volume down", "show me my tabs", "what's the weather"]
)
def test_other_speech_is_not_a_media_question(spoken: str) -> None:
    assert match_media_query(spoken) is None


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("download Dune", MediaAdd("Dune", None)),
        ("Download the movie Heat 1995", MediaAdd("Heat", "radarr", 1995)),
        ("can you download the show Andor", MediaAdd("Andor", "sonarr")),
        ("grab the series Severance", MediaAdd("Severance", "sonarr")),
        ("download blade runner 2049", MediaAdd("blade runner 2049", None)),  # 2049 isn't a plausible year
        ("download Fargo from sonarr", MediaAdd("Fargo", "sonarr")),
        ("add Arrival to radarr", MediaAdd("Arrival", "radarr")),
        ("add the movie Alien to radarr please", None),  # "please" stays in the title: left to the model
        ("download Dune (2021)", MediaAdd("Dune", None, 2021)),
    ],
)
def test_media_add_phrases(spoken: str, expected) -> None:
    result = match_media_add(spoken)
    if expected is None:
        assert result is None or result.title != "Alien"
    else:
        assert result == expected


@pytest.mark.parametrize("spoken", ["download it", "download", "what is downloading", "open spotify", "add milk to my list"])
def test_other_speech_is_not_a_media_add(spoken: str) -> None:
    assert match_media_add(spoken) is None


def make_voice(tool_results):
    calls: list[tuple[str, dict]] = []
    dms: list[str] = []
    model_used: list[int] = []

    class Tools:
        def execute(self, name, arguments):
            calls.append((name, json.loads(arguments)))
            return json.dumps(tool_results(name, json.loads(arguments)))

    class Loop:
        tools = Tools()

        def run(self, conversation):
            model_used.append(1)
            return "model answer"

    session = SimpleNamespace(
        loop=Loop(), conversation_context=ConversationContext(), message_sender=lambda ch, t, ts: dms.append(t),
        run_lock=threading.RLock(), channel_lister=None,
    )
    config = SimpleNamespace(
        settings=SimpleNamespace(allowed_slack_user_id="U"),
        app=SimpleNamespace(voice=SimpleNamespace(reply_to="slack"),
                            conversation=SimpleNamespace(max_messages=30, voice_idle_minutes=10, dm_idle_minutes=120)),
    )
    return build_command_handler(config, session, notifier=lambda *_a: None), calls, dms, model_used


def test_voice_whats_downloading_goes_straight_to_turbo_plex() -> None:
    handler, calls, dms, model_used = make_voice(lambda n, a: {"summary": "SABnzbd: 2 in the queue"})
    handler("what's downloading")
    assert calls == [("media_downloads", {})] and dms[0].endswith("SABnzbd: 2 in the queue") and model_used == []
    handler("what was recently downloaded")
    assert calls[1] == ("media_recent", {})


def test_voice_download_adds_the_title_without_a_second_yes() -> None:
    handler, calls, dms, model_used = make_voice(
        lambda n, a: {"auto_confirmed": "x", "preview": "p", "result": {"status": "added", "service": "radarr", "title": "Heat"}}
    )
    handler("download the movie Heat 1995")
    assert calls == [("propose_add_media", {"title": "Heat", "service": "radarr", "year": 1995})]
    assert dms[0].endswith("Added Heat to Radarr -- it'll start downloading.") and model_used == []


def test_voice_download_that_is_ambiguous_asks_which() -> None:
    handler, _, dms, _ = make_voice(
        lambda n, a: {"status": "ambiguous", "options": [
            {"service": "radarr", "title": "Fargo", "year": 1996, "kind": "movie"},
            {"service": "sonarr", "title": "Fargo", "year": 2014, "kind": "TV series"}]}
    )
    handler("download Fargo")
    assert "Fargo (1996) [movie]; Fargo (2014) [TV series]" in dms[0]


def test_voice_download_with_no_match_falls_through_to_the_model() -> None:
    handler, calls, dms, model_used = make_voice(lambda n, a: {"error": "No match found for 'zzz'"})
    handler("download zzz")
    assert model_used == [1] and dms[-1].endswith("model answer")


def test_voice_download_with_media_not_set_up_says_so() -> None:
    handler, _, dms, model_used = make_voice(lambda n, a: {"error": "Radarr isn't set up yet. These services run on the turbo-plex server"})
    handler("download the movie Heat")
    assert "turbo-plex" in dms[0] and model_used == []


def test_slack_downloads_command(tmp_path) -> None:
    from tests.test_slack_router import make_app_config

    seen: list[str] = []

    class Tools:
        def execute(self, name, arguments):
            seen.append(name)
            return json.dumps({"summary": f"result of {name}"})

    session = SimpleNamespace(loop=SimpleNamespace(tools=Tools()), conversation_context=ConversationContext())
    router = SlackRouter(session=session, config=make_app_config(tmp_path), allowed_user_id="U")
    assert router.handle_text("D1", "!downloads") == "result of media_downloads"
    assert router.handle_text("D1", "!downloads recent") == "result of media_recent"
    assert seen == ["media_downloads", "media_recent"]
