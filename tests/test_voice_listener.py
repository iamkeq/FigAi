from __future__ import annotations

import numpy as np

from figai.voice.listener import FRAME_SIZE, VoiceListener, VoiceListenerConfig, is_silent

LOUD_FRAME = np.full(FRAME_SIZE, 10_000, dtype=np.int16)
SILENT_FRAME = np.zeros(FRAME_SIZE, dtype=np.int16)


class FakeAudioSource:
    def __init__(self, frames: list[np.ndarray]) -> None:
        self._frames = iter(frames)

    def read_frame(self) -> np.ndarray:
        return next(self._frames)


class FakeWakeWordDetector:
    def __init__(self, scores: list[float]) -> None:
        self._scores = iter(scores)

    def score(self, frame: np.ndarray) -> float:
        return next(self._scores)


def test_is_silent_detects_low_amplitude() -> None:
    assert is_silent(SILENT_FRAME) is True
    assert is_silent(LOUD_FRAME) is False


def test_run_once_does_nothing_below_wake_word_threshold() -> None:
    commands: list[str] = []
    listener = VoiceListener(
        audio_source=FakeAudioSource([SILENT_FRAME]),
        wake_word_detector=FakeWakeWordDetector([0.1]),
        transcriber=lambda audio: "should not be called",
        on_command=commands.append,
    )

    listener.run_once()

    assert commands == []


def test_run_once_triggers_recording_and_dispatches_transcribed_command() -> None:
    commands: list[str] = []
    config = VoiceListenerConfig(wake_word_threshold=0.5, silence_seconds=0.16)  # 2 silent frames
    # First frame triggers the wake word; then two loud (speech) frames, then
    # enough silent frames to end the recording.
    frames = [LOUD_FRAME, LOUD_FRAME, LOUD_FRAME, SILENT_FRAME, SILENT_FRAME]
    listener = VoiceListener(
        audio_source=FakeAudioSource(frames),
        wake_word_detector=FakeWakeWordDetector([0.9]),
        transcriber=lambda audio: "fig open obs",
        on_command=commands.append,
        config=config,
    )

    listener.run_once()

    assert commands == ["fig open obs"]


def test_run_once_ignores_empty_transcription() -> None:
    commands: list[str] = []
    config = VoiceListenerConfig(wake_word_threshold=0.5, silence_seconds=0.08)
    frames = [SILENT_FRAME, SILENT_FRAME]
    listener = VoiceListener(
        audio_source=FakeAudioSource(frames),
        wake_word_detector=FakeWakeWordDetector([0.9]),
        transcriber=lambda audio: "   ",
        on_command=commands.append,
        config=config,
    )

    listener.run_once()

    assert commands == []


def test_record_command_stops_at_max_duration_even_without_silence() -> None:
    commands: list[str] = []
    config = VoiceListenerConfig(wake_word_threshold=0.5, silence_seconds=10.0, max_command_seconds=0.16)
    frames = [LOUD_FRAME] * 10  # never goes silent
    listener = VoiceListener(
        audio_source=FakeAudioSource(frames),
        wake_word_detector=FakeWakeWordDetector([0.9]),
        transcriber=lambda audio: "kept talking",
        on_command=commands.append,
        config=config,
    )

    listener.run_once()

    assert commands == ["kept talking"]


def test_run_forever_stops_when_stop_is_called() -> None:
    call_count = 0

    class CountingAudioSource:
        def read_frame(self) -> np.ndarray:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                listener.stop()
            return SILENT_FRAME

    listener = VoiceListener(
        audio_source=CountingAudioSource(),
        wake_word_detector=FakeWakeWordDetector([0.0, 0.0, 0.0, 0.0]),
        transcriber=lambda audio: "",
        on_command=lambda text: None,
    )

    listener.run_forever()

    assert call_count == 3
