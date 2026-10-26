"""Voice front-end: wake-word -> local speech-to-text -> the same agent core
used by Slack/CLI (it even shares the Slack session). Fig doesn't talk back out loud: he acts,
and the reply goes to your Slack DM and/or a desktop notification (voice.reply_to)."""

from __future__ import annotations

import contextlib
import json
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from figai import help as help_menu
from figai.agent.history import is_idle, trim_history
from figai.agent.auto_confirm import ALL_RESOLVERS
from figai.agent.loop import Message
from figai.agent.session import AgentSession
from figai.config import Config
from figai.logging_setup import get_logger
from figai.tools.notifications import default_notifier
from figai.voice.chime import play_listening_chime
from figai.voice.outcome import classify
from figai.voice.shortcuts import (
    is_help_phrase,
    is_no_phrase,
    is_reset_phrase,
    is_yes_phrase,
    match_focus_window,
    match_media_add,
    match_media_query,
    match_open_app,
    match_slack_post,
    match_window_action,
)
from figai.voice.listener import VoiceListener, VoiceListenerConfig
from figai.voice.speech import Speaker

logger = get_logger("voice")


def wake_phrase_words(model_name: str) -> tuple[str, ...]:
    """Words that are just the wake phrase, so a transcript of only "hey jarvis" is ignored."""
    stem = Path(model_name).stem
    return (*VoiceListenerConfig.wake_phrase_words, *[w for w in stem.lower().replace("-", "_").split("_") if w])


def build_command_handler(
    config: Config,
    session: AgentSession,
    notifier: Callable[[str, str], None] = default_notifier,
    clock: Callable[[], float] = time.time,
    speaker: Speaker | None = None,
) -> Callable[[str], None]:
    conversation: list[Message] = []
    last_command_at: list[float | None] = [None]
    owner = config.settings.allowed_slack_user_id
    reply_to = config.app.voice.reply_to
    settings = config.app.conversation

    def _sound(kind: str | None, preview: str | None = None) -> None:
        """Spoken feedback: "done" -> All set, sir; "fail" -> didn't work; "confirm" -> reads what's about
        to happen, then asks to proceed; "cancelled" -> Very well. None/"answer" stays quiet."""
        if speaker is None or kind is None:
            return
        if kind == "confirm":
            speaker.say_then(preview or "", "confirm")
        elif kind in ("done", "fail", "cancelled"):
            speaker.play(kind)

    def _deliver(text: str, reply: str, outcome: str | None = None, preview: str | None = None) -> None:
        """Spoken commands answer where you'd expect: in your Slack DM (so the reply is readable
        and any posting, screenshots or reminders work like they do from Slack), with a desktop
        notification as the fallback or the extra, per voice.reply_to."""
        _sound(outcome, preview)
        sent = False
        if reply_to in ("slack", "both") and session.message_sender is not None:
            try:
                session.message_sender(owner, f":microphone: _{text}_\n{reply}", None)
                sent = True
            except Exception:
                logger.exception("Could not send the voice reply to Slack")
        if reply_to in ("notification", "both") or not sent:
            try:
                notifier("Fig", reply)
            except Exception:
                logger.exception("Failed to show voice reply notification")

    def _channel_names() -> list[str]:
        lister = getattr(session, "channel_lister", None)
        if lister is None:
            return []
        try:
            return [c["name"] for c in lister()]
        except Exception:
            logger.exception("Could not list Slack channels for voice matching")
            return []

    def _try_focus(text: str) -> bool:
        app = match_focus_window(text)
        if app is None:
            return False
        auto = getattr(session, "auto_confirm", None)
        with session.run_lock, (auto.voice_turn() if auto else contextlib.nullcontext()):
            result = session.loop.tools.execute("focus_window", json.dumps({"name": app}))
        logger.info("Voice shortcut focus -> %s: %s", app, result[:200])
        try:
            data = json.loads(result)
        except ValueError:
            data = {}
        outcome: str | None = "done"
        if data.get("status") == "focused":
            reply = f"Brought up {data.get('app', app)}."
        elif data.get("status") == "launched":
            reply = f"{data.get('program', app)} wasn't open, so I started it."
        elif data.get("status") == "asked":
            reply, outcome = data.get("note", f"I asked Windows to show {app}."), None
        else:
            reply, outcome = f"I couldn't bring up {app}: {data.get('error', result)}", "fail"
        _deliver(text, reply, outcome)
        return True

    def _tool(name: str, arguments: dict) -> dict:
        auto = getattr(session, "auto_confirm", None)
        with session.run_lock, (auto.voice_turn() if auto else contextlib.nullcontext()):
            result = session.loop.tools.execute(name, json.dumps(arguments))
        logger.info("Voice shortcut %s %s -> %s", name, arguments, result[:200])
        try:
            data = json.loads(result)
        except ValueError:
            data = {}
        return data if isinstance(data, dict) else {}

    def _try_window_action(text: str) -> bool:
        match = match_window_action(text)
        if match is None:
            return False
        target = match.target
        if match.action == "close":
            if target == "current":  # close whatever app is in front
                windows = _tool("list_windows", {}).get("windows") or []
                if not windows:
                    _deliver(text, "I can't tell which window is in front.", "fail")
                    return True
                target = windows[0]["app"]
            data = _tool("close_program", {"name": target})
            outcome: str | None = "done"
            if data.get("status") == "closed":
                reply = f"Closed {data.get('program', target)}."
            elif data.get("status") == "asked_to_close":
                reply = f"I asked {data.get('program', target)} to close, but it's still open -- it may be waiting on a save prompt."
                outcome = None
            else:
                reply, outcome = f"I couldn't close {target}: {data.get('error', 'unknown error')}", "fail"
        else:
            data = _tool(f"{match.action}_window", {"name": "" if target == "current" else target})
            if data.get("status") in ("minimized", "maximized"):
                reply, outcome = f"{data['status'].capitalize()} {data.get('app', target)}.", "done"
            else:
                reply, outcome = f"I couldn't {match.action} {target}: {data.get('error', 'unknown error')}", "fail"
        _deliver(text, reply, outcome)
        return True

    def _pending() -> tuple[str, str, str] | None:
        store = getattr(session, "confirmations", None)
        return store.latest() if store is not None else None

    def _confirm(text: str, pending: tuple[str, str, str]) -> None:
        """You said yes to the action Fig just described: carry it out."""
        token, action_type, preview = pending
        resolver = ALL_RESOLVERS.get(action_type)
        if resolver is None:
            _deliver(text, f"I don't know how to carry out '{action_type}' by voice -- use Slack.", "fail")
            return
        data = _tool(resolver, {"token": token})
        if data.get("error"):
            _deliver(text, f"That didn't work: {data['error']}", "fail")
        else:
            _deliver(text, f"Done: {preview}", "done")

    def _try_media(text: str) -> bool:
        kind = match_media_query(text)
        if kind is not None:
            data = _tool("media_downloads" if kind == "current" else "media_recent", {})
            _deliver(text, data.get("summary") or data.get("error", "I couldn't reach the media server."), "fail" if data.get("error") else None)
            return True
        add = match_media_add(text)
        if add is None:
            return False
        arguments: dict = {"title": add.title}
        if add.service:
            arguments["service"] = add.service
        if add.year:
            arguments["year"] = add.year
        data = _tool("propose_add_media", arguments)
        added = data.get("result", data)
        if added.get("status") == "added":
            _deliver(text, f"Added {added.get('title')} to {str(added.get('service')).capitalize()} -- it'll start downloading.", "done")
        elif data.get("status") == "awaiting_confirmation":
            _deliver(text, f"{data.get('preview')}. Say the wake word, then yes, to add it.", "confirm", data.get("preview"))
        elif data.get("status") == "ambiguous":
            options = "; ".join(
                f"{o.get('title')} ({o.get('year')})" + (f" [{o['kind']}]" if o.get("kind") else "") for o in data.get("options", [])
            )
            _deliver(text, f"More than one match: {options}. Tell me which one (add the year, or say movie or show).")
        elif str(data.get("error", "")).startswith("No match"):
            return False  # maybe it wasn't a title request at all: let the model have a go
        else:
            _deliver(text, f"I couldn't add that: {data.get('error', 'unknown error')}", "fail")
        return True

    def _try_open(text: str) -> bool:
        app = match_open_app(text)
        if app is None:
            return False
        data = _tool("open_program", {"name": app})
        if data.get("status") == "launched":
            _deliver(text, f"Opened {data.get('program', app)}.", "done")
            return True
        if data.get("candidates"):
            _deliver(text, f"'{app}' could mean {', '.join(data['candidates'])} -- which one?")
            return True
        return False  # not an installed app (maybe a website or a file): let the model handle it

    def _try_shortcut(text: str) -> bool:
        """Posting/DM and window phrasings are handled in code so they work with any chat model."""
        if _try_focus(text) or _try_window_action(text) or _try_media(text) or _try_open(text):
            return True
        post = match_slack_post(text, _channel_names())
        if post is None:
            return False
        arguments = json.dumps({"text": post.message, "channel": post.channel})
        auto = getattr(session, "auto_confirm", None)
        with session.run_lock, (auto.voice_turn() if auto else contextlib.nullcontext()):
            result = session.loop.tools.execute("post_slack_message", arguments)
        logger.info("Voice shortcut post -> %s: %s", post.channel, result[:200])
        try:
            data = json.loads(result)
        except ValueError:
            data = {}
        result_data = data.get("result", data)
        preview = None
        if result_data.get("status") == "posted":
            reply, outcome = f"Posted to {result_data.get('to', post.channel)}: {post.message}", "done"
        elif data.get("status") == "awaiting_confirmation":
            preview = data.get("preview")
            reply, outcome = f"{preview}. Say the wake word, then yes, to send it.", "confirm"
        else:
            reply, outcome = f"I couldn't post that: {result_data.get('error', result)}", "fail"
        _deliver(text, reply, outcome, preview)
        return True

    def _on_command(text: str) -> None:
        logger.info("Voice command: %s", text)
        now = clock()
        if is_idle(last_command_at[0], now, settings.voice_idle_minutes) and conversation:
            logger.info("Voice session idle for over %s min -- starting fresh", settings.voice_idle_minutes)
            conversation.clear()
        last_command_at[0] = now

        pending = _pending()
        if pending and is_yes_phrase(text):
            _confirm(text, pending)
            return
        if pending and is_no_phrase(text):
            session.confirmations.cancel(pending[0])
            _deliver(text, f"Cancelled: {pending[2]}", "cancelled")
            return
        if speaker is not None:
            speaker.play("ack")

        if is_help_phrase(text):
            try:
                _deliver(text, help_menu.menu(session.loader.get_instructions("help")))
            except Exception:
                logger.exception("Could not build the help menu")
                _deliver(text, "I couldn't load the help menu -- try `!help` in Slack.")
            return
        if is_reset_phrase(text):
            conversation.clear()
            _deliver(text, "Fresh start -- I've forgotten our conversation.", "done")
            return
        if _try_shortcut(text):
            return
        conversation.append({"role": "user", "content": text})
        trim_history(conversation, settings.max_messages)
        try:
            # Same session (and lock) as Slack; tools that need "the current channel" mean your DM.
            auto = getattr(session, "auto_confirm", None)
            observe = getattr(getattr(session.loop, "tools", None), "observe", None)
            with session.run_lock, (auto.voice_turn() if auto else contextlib.nullcontext()), (
                observe() if observe else contextlib.nullcontext([])
            ) as seen:
                session.conversation_context.current_channel = owner
                session.conversation_context.current_thread_ts = None
                reply = session.loop.run(conversation)
        except Exception:
            logger.exception("Agent loop failed on voice command")
            conversation.pop()
            if speaker is not None:
                speaker.play("fail")
            return

        conversation.append({"role": "assistant", "content": reply})
        logger.info("Voice reply: %s", reply)
        kind, preview = classify(seen or [])
        _deliver(text, reply, kind, preview)

    return _on_command


def run_voice_frontend(config: Config, session: Optional[AgentSession] = None) -> None:
    if not config.app.voice.enabled:
        logger.info("Voice front-end disabled (set voice.enabled: true in config.yaml)")
        return

    from figai.voice.microphone import MicrophoneAudioSource
    from figai.voice.transcribe import FasterWhisperTranscriber
    from figai.voice.wake_word import OpenWakeWordDetector

    session = session or AgentSession(config)
    speaker = None
    if config.app.voice.feedback:
        speaker = Speaker(
            Path(config.app.state_path).parent / "voice_cache",
            honorific=config.app.voice.honorific,
            voice=config.app.voice.speech_voice,
            rate=config.app.voice.speech_rate,
        )
        threading.Thread(target=speaker.prepare, name="fig-speech-prep", daemon=True).start()
    _on_command = build_command_handler(config, session, speaker=speaker)

    audio_source = MicrophoneAudioSource()
    wake_word_detector = OpenWakeWordDetector(config.app.voice.wake_word_model)
    transcriber = FasterWhisperTranscriber(config.app.voice.whisper_model)

    listener = VoiceListener(
        audio_source=audio_source,
        wake_word_detector=wake_word_detector,
        transcriber=transcriber,
        on_command=_on_command,
        on_wake=play_listening_chime if config.app.voice.chime else None,
        config=VoiceListenerConfig(
            wake_word_threshold=config.app.voice.wake_word_threshold,
            silence_seconds=config.app.voice.silence_seconds,
            max_command_seconds=config.app.voice.max_command_seconds,
            no_speech_timeout_seconds=config.app.voice.no_speech_timeout_seconds,
            wake_phrase_words=wake_phrase_words(config.app.voice.wake_word_model),
        ),
    )

    logger.info("Voice front-end listening for wake word '%s'", config.app.voice.wake_word_model)
    listener.run_forever()
