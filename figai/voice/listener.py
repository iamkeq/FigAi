"""Core wake-word listening loop: score each audio frame for the wake word,
and once triggered, record until a pause in speech and hand the recording to a
transcriber. Pure and fully injectable so it can be tested without any real
microphone, wake-word model, or speech-to-text engine -- those live in
microphone.py / wake_word.py / transcribe.py as thin real adapters."""

from __future__ import annotations

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


@dataclass
class VoiceListener:
    audio_source: AudioSource
    wake_word_detector: WakeWordDetector
    transcriber: Transcriber
    on_command: CommandHandler
    config: VoiceListenerConfig = field(default_factory=VoiceListenerConfig)
    _stop: bool = field(default=False, init=False, repr=False)

    def stop(self) -> None:
        self._stop = True

    def run_once(self) -> None:
        """Processes exactly one frame. Exposed separately from `run_forever` so
        tests can drive the loop deterministically, frame by frame."""
        frame = self.audio_source.read_frame()
        score = self.wake_word_detector.score(frame)
        if score < self.config.wake_word_threshold:
            return

        command_audio = self._record_command()
        if command_audio is None:
            return

        text = self.transcriber(command_audio).strip()
        if text:
            self.on_command(text)

    def _record_command(self) -> np.ndarray | None:
        frames: list[np.ndarray] = []
        silence_run = 0.0
        elapsed = 0.0

        while elapsed < self.config.max_command_seconds:
            frame = self.audio_source.read_frame()
            frames.append(frame)
            elapsed += FRAME_SECONDS

            if is_silent(frame):
                silence_run += FRAME_SECONDS
                if frames and silence_run >= self.config.silence_seconds:
                    break
            else:
                silence_run = 0.0

        return np.concatenate(frames) if frames else None

    def run_forever(self) -> None:
        while not self._stop:
            self.run_once()
