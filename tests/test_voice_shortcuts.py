from __future__ import annotations

import pytest

from figai.voice.shortcuts import SlackPost, match_slack_post


@pytest.mark.parametrize(
    "spoken,expected",
    [
        ("Type in general the word potato.", SlackPost("general", "potato")),
        ("Post in general that you can hear me.", SlackPost("general", "you can hear me")),
        ("post in chat that dinner is at six", SlackPost("chat", "dinner is at six")),
        ("Hey, please type in the general chat hello everyone", SlackPost("general chat", "hello everyone")),
        ("say in the chat: this is a test", SlackPost("chat", "this is a test")),
        ("Post to the dev channel that the build is done", SlackPost("dev", "the build is done")),
        ("send a message to general saying good morning", SlackPost("general", "good morning")),
        ("Post and share in general that it works", None),
        ("DM me that the miner is up", SlackPost("me", "the miner is up")),
        ("message me the number 42", SlackPost("me", "42")),
        ("Send me a DM saying call mom", SlackPost("me", "call mom")),
    ],
)
def test_phrasings_that_should_post(spoken: str, expected) -> None:
    assert match_slack_post(spoken, ["dev", "social"]) == expected


@pytest.mark.parametrize(
    "spoken",
    [
        "What time is it",
        "How many tabs do I have open",
        "Can you hear me and can you type in general?",
        "type in general",  # nothing to say
        "open spotify",
        "tell me a joke",
        "post in nowhere that hi",  # unknown channel: leave it to the model
        "OBS.",
    ],
)
def test_other_speech_is_left_to_the_model(spoken: str) -> None:
    assert match_slack_post(spoken, ["dev"]) is None


def test_channel_names_with_regex_characters_are_safe() -> None:
    assert match_slack_post("post in c++ that hi", ["c++"]) == SlackPost("c++", "hi")
