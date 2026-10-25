"""Spoken commands that are matched in plain code instead of being left to the chat model.

A small or free chat model often answers "type in general the word potato" with the word
"potato" in the reply, instead of calling the posting tool. For the handful of phrasings that
are unambiguous -- posting to a channel, DMing you -- the words are parsed here and the tool is
called directly, so they work the same whatever model is selected."""

from __future__ import annotations

import re
from dataclasses import dataclass

from figai.tools.slack_post import CHAT_ALIASES

VERBS = r"(?:post|type|say|write|send|put|share|announce|tell)"
FILLER = r"^(?:(?:hey|ok|okay|please|fig|can you|could you|i want you to|go ahead and)[,.\s]+)*"
LEAD_IN = r"(?:(?:that|saying|the word|the words|the message|the text|the number)\s+)?"
DM_PATTERN = re.compile(
    rf"{FILLER}(?:(?:dm|direct message|message|ping|text)\s+me|send me a (?:dm|direct message))"
    rf"[,.:;\-\s]+(?:(?:that|saying|the word|the words|the number)\s+)?(?P<message>.+)$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class SlackPost:
    channel: str  # a channel name, an alias like "chat", or "me" for a DM
    message: str


def _clean(message: str) -> str:
    return message.strip().strip('"“”').rstrip(".").strip()


def match_slack_post(text: str, channel_names: list[str] | tuple[str, ...] = ()) -> SlackPost | None:
    """"type in general the word potato" -> SlackPost("general", "potato"); None if it isn't that."""
    text = text.strip()
    names = sorted({*CHAT_ALIASES, "general", *channel_names}, key=len, reverse=True)
    channel = "|".join(re.escape(n) for n in names)
    pattern = re.compile(
        rf"{FILLER}{VERBS}\s+(?:a\s+message\s+)?(?:in|to|on|into)\s+(?:the\s+)?(?P<channel>{channel})(?:\s+channel)?"
        rf"[,.:;\-\s]+{LEAD_IN}(?P<message>.+)$",
        re.IGNORECASE,
    )
    found = pattern.match(text)
    if found:
        message = _clean(found.group("message"))
        return SlackPost(found.group("channel").lower(), message) if message else None
    dm = DM_PATTERN.match(text)
    if dm:
        message = _clean(dm.group("message"))
        return SlackPost("me", message) if message else None
    return None


RESET_PHRASES = {
    "new session", "new conversation", "start over", "start a new session", "start a new conversation",
    "start a fresh conversation", "reset", "reset the conversation", "reset the session", "reset session",
    "reset conversation", "forget everything", "forget that", "clear the conversation", "clear the session",
    "fresh start", "start fresh",
}


def is_reset_phrase(text: str) -> bool:
    """"start over", "new session", ... -- forget the voice conversation so far."""
    words = re.findall(r"[a-z']+", text.lower())
    while words and words[0] in ("hey", "ok", "okay", "please", "fig", "can", "you", "could"):
        words.pop(0)
    return " ".join(words) in RESET_PHRASES


HELP_PHRASES = {
    "help", "help menu", "show help", "show the help menu", "what can you do", "what do you do",
    "what are your abilities", "what are your commands", "list your abilities", "list your commands",
    "show me what you can do", "how do i use you",
}


def is_help_phrase(text: str) -> bool:
    words = re.findall(r"[a-z']+", text.lower())
    while words and words[0] in ("hey", "ok", "okay", "please", "fig"):
        words.pop(0)
    return " ".join(words) in HELP_PHRASES


NOT_AN_APP = {"me", "us", "him", "her", "them", "it", "that", "this", "these", "those", "my", "your", "a", "an"}
FOCUS_PATTERN = re.compile(
    rf"{FILLER}(?:please\s+)?"
    r"(?:(?:bring\s+up|pull\s+up|pop\s+up|switch\s+to|focus\s+on|focus|bring)\s+(?:the\s+)?(?P<app>[a-z0-9+#.'\- ]{2,40}?))"
    r"(?:\s+(?:up|window|please|to\s+the\s+(?:front|foreground)|forward|on\s+(?:the\s+)?screen))*[.!?]?\s*$",
    re.IGNORECASE,
)


def match_focus_window(text: str) -> str | None:
    """"bring discord up" / "pull up spotify" / "switch to chrome" -> the app name, else None."""
    found = FOCUS_PATTERN.match(text.strip())
    if not found:
        return None
    app = re.sub(r"\s+", " ", found.group("app")).strip(" .").lower()
    if not app or app.split()[0] in NOT_AN_APP:
        return None
    return app


WINDOW_ACTION_PATTERN = re.compile(
    rf"{FILLER}(?:please\s+)?(?P<verb>minimi[sz]e|maximi[sz]e|close|quit|exit)\s+(?:the\s+)?"
    r"(?P<app>[a-z0-9+#.'\- ]{2,40}?)(?:\s+(?:window|windows|app|application|program|please))*[.!?]?\s*$",
    re.IGNORECASE,
)
VERB_ACTIONS = {"minimize": "minimize", "minimise": "minimize", "maximize": "maximize", "maximise": "maximize",
                "close": "close", "quit": "close", "exit": "close"}
CURRENT_PHRASES = {"this", "current", "active", "this one", "current window", "active window", "the current", "the active"}
ALL_PHRASES = {"all", "everything", "all windows", "all of them", "every window", "all the windows"}


@dataclass(frozen=True)
class WindowAction:
    action: str  # minimize | maximize | close
    target: str  # an app name, or "current", or "all"


def match_window_action(text: str) -> WindowAction | None:
    """"minimize discord", "maximize chrome", "close spotify", "minimize everything", "close this"."""
    found = WINDOW_ACTION_PATTERN.match(text.strip())
    if not found:
        return None
    action = VERB_ACTIONS[found.group("verb").lower()]
    app = re.sub(r"\s+", " ", found.group("app")).strip(" .").lower()
    if not app or app.split()[0] in NOT_AN_APP - {"this"}:
        return None
    if app in CURRENT_PHRASES or app == "this window":
        return WindowAction(action, "current")
    if app in ALL_PHRASES:
        return None if action != "minimize" else WindowAction(action, "all")  # never "close everything"
    return WindowAction(action, app)

OPEN_PATTERN = re.compile(
    rf"{FILLER}(?:please\s+)?(?:open(?:\s+up)?|launch)\s+(?:the\s+|up\s+)?(?P<app>[a-z0-9+#.'\- ]{{2,40}}?)"
    r"(?:\s+(?:app|application|program|please|for me))*[.!?]?\s*$",
    re.IGNORECASE,
)
NOT_AN_APP_TARGET = re.compile(r"(?:^|\s)(?:tab|tabs|window|file|folder|link|url|website|page|door|email|emails|mail)(?:\s|$)|\.[a-z]{2,4}$|^(?:http|www)", re.IGNORECASE)


def match_open_app(text: str) -> str | None:
    """"open notepad" / "launch spotify" -> the app name. Sites, tabs, files and folders are left to the model."""
    found = OPEN_PATTERN.match(text.strip())
    if not found:
        return None
    app = re.sub(r"\s+", " ", found.group("app")).strip(" .").lower()
    if not app or app.split()[0] in NOT_AN_APP or len(app.split()) > 4 or NOT_AN_APP_TARGET.search(app):
        return None
    return app


YES_PHRASES = {
    "yes", "yeah", "yep", "yup", "yes please", "yes do it", "do it", "go ahead", "go for it", "confirm", "confirmed",
    "proceed", "sure", "affirmative", "yes sir", "absolutely", "please do", "okay do it", "ok do it", "that's right",
}
NO_PHRASES = {
    "no", "nope", "no thanks", "no thank you", "cancel", "cancel that", "never mind", "nevermind", "don't", "do not",
    "stop", "abort", "negative", "forget it", "no sir", "don't do it", "do not do it",
}


def _plain(text: str) -> str:
    words = re.findall(r"[a-z']+", text.lower())
    while words and words[0] in ("hey", "ok", "okay", "fig", "jarvis") and " ".join(words) not in YES_PHRASES | NO_PHRASES:
        words.pop(0)
    return " ".join(words)


YES_LEADS = {"yes", "yeah", "yep", "yup", "sure", "proceed", "confirm", "confirmed", "affirmative", "absolutely"}
NO_LEADS = {"no", "nope", "cancel", "negative", "stop", "abort", "nevermind"}
LEAD_MAX_WORDS = 3  # "no, cancel that" / "yes sir please" -- but not "yes what time is it"


def _says(text: str, phrases: set[str], leads: set[str]) -> bool:
    plain = _plain(text)
    words = plain.split()
    return plain in phrases or (0 < len(words) <= LEAD_MAX_WORDS and words[0] in leads)


def is_yes_phrase(text: str) -> bool:
    return _says(text, YES_PHRASES, YES_LEADS)


def is_no_phrase(text: str) -> bool:
    return _says(text, NO_PHRASES, NO_LEADS)


QUESTION_LEAD = re.compile(r"^(?:(?:hey|ok|okay|please|fig|can you|could you|tell me|show me|let me know)[, ]+)*(?:what|which|how|show|list|check|is there|are there|any|give me|anything)\b", re.IGNORECASE)
RECENT_STRONG = re.compile(
    r"\b(?:recent|recently|lately|finished|completed|last|yesterday|today|came in|did you download|has been downloaded|"
    r"was downloaded|were downloaded|got downloaded|have been downloaded)\b", re.IGNORECASE,
)
CURRENT_NOW = re.compile(r"\b(?:downloading|being downloaded|download queue|in the queue|on the queue|on sab(?:nzbd)?|sab(?:nzbd)?|queue|right now|currently)\b", re.IGNORECASE)


def match_media_query(text: str) -> str | None:
    """"what's downloading" -> "current"; "what was recently downloaded" -> "recent"; else None."""
    text = text.strip()
    if not QUESTION_LEAD.match(text):
        return None
    mentions = re.search(r"\bdownload|\bsab(?:nzbd)?\b|\bqueue\b", text, re.IGNORECASE)
    if mentions is None:
        return None
    if RECENT_STRONG.search(text):
        return "recent"
    if CURRENT_NOW.search(text) or re.search(r"\bdownloads\b", text, re.IGNORECASE):
        return "current"
    return "recent" if re.search(r"\bdownloaded\b", text, re.IGNORECASE) else None


@dataclass(frozen=True)
class MediaAdd:
    title: str
    service: str | None  # "radarr" (movie), "sonarr" (TV) or None = search both
    year: int | None = None


MEDIA_ADD_PATTERNS = (
    re.compile(
        rf"{FILLER}(?:please\s+)?(?:download|grab)(?:\s+me)?(?:\s+(?:the|a|an))?(?:\s+(?P<kind>movie|film|show|series|tv show|tv series|season of))?"
        r"\s+(?P<title>.+?)(?:\s+(?:in|on|from|to|with|using)\s+(?P<service>sonarr|radarr))?[.!?]?\s*$",
        re.IGNORECASE,
    ),
    re.compile(
        rf"{FILLER}(?:please\s+)?add\s+(?:the\s+)?(?:(?P<kind>movie|film|show|series|tv show)\s+)?(?P<title>.+?)\s+(?:to|in|on)\s+(?P<service>sonarr|radarr)[.!?]?\s*$",
        re.IGNORECASE,
    ),
)
YEAR_AT_END = re.compile(r"^(?P<title>.+?)\s+\(?(?P<year>(?:19|20)\d{2})\)?$")
MOVIE_WORDS = {"movie", "film"}
SHOW_WORDS = {"show", "series", "tv show", "tv series", "season of"}


def match_media_add(text: str) -> MediaAdd | None:
    """"download Dune", "download the movie Heat 1995", "grab the show Andor", "add Fargo to sonarr"."""
    for pattern in MEDIA_ADD_PATTERNS:
        found = pattern.match(text.strip())
        if not found:
            continue
        title = found.group("title").strip(" .\"")
        kind = (found.group("kind") or "").lower()
        service = (found.group("service") or "").lower() or ("radarr" if kind in MOVIE_WORDS else "sonarr" if kind in SHOW_WORDS else None)
        year = None
        with_year = YEAR_AT_END.match(title)
        if with_year and 1900 <= int(with_year.group("year")) <= 2030:
            title, year = with_year.group("title").strip(), int(with_year.group("year"))
        words = title.lower().split()
        if not title or len(words) > 8 or words[0] in NOT_AN_APP or title.lower() in ("it", "that", "this", "them"):
            return None
        return MediaAdd(title, service or None, year)
    return None
