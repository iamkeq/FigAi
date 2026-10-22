"""Core wake-word listening loop: score each audio frame for the wake word,
and once triggered, record until a pause in speech and hand the recording to a
transcriber. Pure and fully injectable so it can be tested without any real
microphone, wake-word model, or speech-to-text engine -- those live in
microphone.py / wake_word.py / transcribe.py as thin real adapters."""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from typing import Callable, Protocol

import numpy as np

SAMPLE_RATE = 16000
FRAME_SIZE = 1280  # openWakeWord's expected chunk size: 80ms of 16kHz mono audio
FRAME_SECONDS = FRAME_SIZE / SAMPLE_RATE
SILENCE_AMPLITUDE_THRESHOLD = 500


class AudioSource(Protocol):
    def read_frame(self) -> np.ndarray:
        """Blocks until one FRAME_SIZE-sample mono int16 frame is available."""
        ...


class WakeWordDetector(Protocol):
    def score(self, frame: np.ndarray) -> float:
        """Confidence (0..1) that `frame` contains the end of the wake word."""
        ...


Transcriber = Callable[[np.ndarray], str]
CommandHandler = Callable[[str], None]


def is_silent(frame: np.ndarray, threshold: int = SILENCE_AMPLITUDE_THRESHOLD) -> bool:
    return int(np.abs(frame).max()) < threshold


@dataclass
class VoiceListenerConfig:
    wake_word_threshold: float = 0.5
    silence_seconds: float = 1.2
    max_command_seconds: float = 15.0
    # After the wake word, how long to wait for you to START talking before giving up.
    no_speech_timeout_seconds: float = 5.0
    # After a command, ignore the wake word briefly so the tail of the speech, the chime and any
    # audio queued up while Fig was thinking can't trigger a second command.
    cooldown_seconds: float = 1.5
    # Words that are just the wake phrase ("hey jarvis"); a transcript made only of these is dropped.
    wake_phrase_words: tuple[str, ...] = ("hey", "hi", "ok", "okay", "jarvis", "fig")


def is_only_wake_phrase(text: str, words: tuple[str, ...]) -> bool:
    tokens = re.findall(r"[a-z']+", text.lower())
    return all(token in words for token in tokens)


@dataclass
class VoiceListener:
    audio_source: AudioSource
    wake_word_detector: WakeWordDetector
    transcriber: Transcriber
    on_command: CommandHandler
    config: VoiceListenerConfig = field(default_factory=VoiceListenerConfig)
    on_wake: Callable[[], None] | None = None  # e.g. play a chime: "I'm listening"
    clock: Callable[[], float] = time.monotonic
    _stop: bool = field(default=False, init=False, repr=False)
    _quiet_until: float = field(default=0.0, init=False, repr=False)

    def stop(self) -> None:
        self._stop = True

    def run_once(self) -> None:
        """Processes exactly one frame. Exposed separately from `run_forever` so
        tests can drive the loop deterministically, frame by frame."""
        frame = self.audio_source.read_frame()
        if self.clock() < self._quiet_until:
            return  # cooling down after a command: heard audio doesn't count
        score = self.wake_word_detector.score(frame)
        if score < self.config.wake_word_threshold:
            return

        if self.on_wake is not None:
            self.on_wake()
        self._reset_listening()  # drop the wake word itself (and the chime) from what gets recorded
        command_audio = self._record_command()
        text = self.transcriber(command_audio).strip() if command_audio is not None else ""
        if text and not is_only_wake_phrase(text, self.config.wake_phrase_words):
            self.on_command(text)
        self._reset_listening()
        self._quiet_until = self.clock() + self.config.cooldown_seconds

    def _reset_listening(self) -> None:
        """Throws away audio queued while we were busy and clears the wake-word model's memory,
        so one "hey jarvis" can't fire again on its own echo or on a backlog of old audio."""
        drain = getattr(self.audio_source, "drain", None)
        if callable(drain):
            drain()
        reset = getattr(self.wake_word_detector, "reset", None)
        if callable(reset):
            reset()

    def _record_command(self) -> np.ndarray | None:
        """Waits (up to no_speech_timeout) for you to start talking, then records until you pause.
        Returns None if you never spoke."""
        frames: list[np.ndarray] = []
        waited = silence_run = elapsed = 0.0
        heard_speech = False

        while elapsed < self.config.max_command_seconds:
            frame = self.audio_source.read_frame()
            elapsed += FRAME_SECONDS

            if not is_silent(frame):
                heard_speech = True
                silence_run = 0.0
                frames.append(frame)
            elif heard_speech:
                frames.append(frame)
                silence_run += FRAME_SECONDS
                if silence_run >= self.config.silence_seconds:
                    break
            else:
                waited += FRAME_SECONDS
                if waited >= self.config.no_speech_timeout_seconds:
                    return None

        return np.concatenate(frames) if heard_speech else None

    def run_forever(self) -> None:
        while not self._stop:
            self.run_once()
