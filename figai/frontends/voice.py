"""Voice front-end: wake-word -> local speech-to-text -> the same agent core
used by Slack/CLI. Text-only for now -- Fig doesn't talk back, it acts and
shows a desktop notification with its reply."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

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


def run_voice_frontend(config: Config, session: Optional[AgentSession] = None) -> None:
    if not config.app.voice.enabled:
        logger.info("Voice front-end disabled (set voice.enabled: true in config.yaml)")
        return

    from figai.voice.microphone import MicrophoneAudioSource
    from figai.voice.transcribe import FasterWhisperTranscriber
    from figai.voice.wake_word import OpenWakeWordDetector

    session = session or AgentSession(config)
    conversation: list[Message] = []

    def _on_command(text: str) -> None:
        logger.info("Voice command: %s", text)
        conversation.append({"role": "user", "content": text})
        try:
            reply = session.loop.run(conversation)
        except Exception:
            logger.exception("Agent loop failed on voice command")
            conversation.pop()
            return

        conversation.append({"role": "assistant", "content": reply})
        logger.info("Voice reply: %s", reply)
        try:
            default_notifier("Fig", reply)
        except Exception:
            logger.exception("Failed to show voice reply notification")

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
