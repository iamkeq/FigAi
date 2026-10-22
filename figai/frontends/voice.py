"""Voice front-end: wake-word -> local speech-to-text -> the same agent core
used by Slack/CLI (it even shares the Slack session). Fig doesn't talk back out loud: he acts,
and the reply goes to your Slack DM and/or a desktop notification (voice.reply_to)."""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Optional

from figai.agent.loop import Message
from figai.agent.session import AgentSession
from figai.config import Config
from figai.logging_setup import get_logger
from figai.tools.notifications import default_notifier
from figai.voice.chime import play_listening_chime
from figai.voice.listener import VoiceListener, VoiceListenerConfig

logger = get_logger("voice")


def wake_phrase_words(model_name: str) -> tuple[str, ...]:
    """Words that are just the wake phrase, so a transcript of only "hey jarvis" is ignored."""
    stem = Path(model_name).stem
    return (*VoiceListenerConfig.wake_phrase_words, *[w for w in stem.lower().replace("-", "_").split("_") if w])


def build_command_handler(
    config: Config, session: AgentSession, notifier: Callable[[str, str], None] = default_notifier
) -> Callable[[str], None]:
    conversation: list[Message] = []
    owner = config.settings.allowed_slack_user_id
    reply_to = config.app.voice.reply_to

    def _deliver(text: str, reply: str) -> None:
        """Spoken commands answer where you'd expect: in your Slack DM (so the reply is readable
        and any posting, screenshots or reminders work like they do from Slack), with a desktop
        notification as the fallback or the extra, per voice.reply_to."""
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

    def _on_command(text: str) -> None:
        logger.info("Voice command: %s", text)
        conversation.append({"role": "user", "content": text})
        try:
            # Same session (and lock) as Slack; tools that need "the current channel" mean your DM.
            with session.run_lock:
                session.conversation_context.current_channel = owner
                session.conversation_context.current_thread_ts = None
                reply = session.loop.run(conversation)
        except Exception:
            logger.exception("Agent loop failed on voice command")
            conversation.pop()
            return

        conversation.append({"role": "assistant", "content": reply})
        logger.info("Voice reply: %s", reply)
        _deliver(text, reply)

    return _on_command


def run_voice_frontend(config: Config, session: Optional[AgentSession] = None) -> None:
    if not config.app.voice.enabled:
        logger.info("Voice front-end disabled (set voice.enabled: true in config.yaml)")
        return

    from figai.voice.microphone import MicrophoneAudioSource
    from figai.voice.transcribe import FasterWhisperTranscriber
    from figai.voice.wake_word import OpenWakeWordDetector

    session = session or AgentSession(config)
    _on_command = build_command_handler(config, session)

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
