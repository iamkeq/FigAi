from __future__ import annotations

import numpy as np

from figai.voice.listener import FRAME_SIZE, VoiceListener, VoiceListenerConfig, is_only_wake_phrase, is_silent

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
    frames = [SILENT_FRAME, LOUD_FRAME, SILENT_FRAME, SILENT_FRAME]
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


class RecordingSource(FakeAudioSource):
    def __init__(self, frames: list[np.ndarray]) -> None:
        super().__init__(frames)
        self.drains = 0

    def drain(self) -> None:
        self.drains += 1


class RecordingDetector(FakeWakeWordDetector):
    def __init__(self, scores: list[float]) -> None:
        super().__init__(scores)
        self.resets = 0

    def reset(self) -> None:
        self.resets += 1


def test_chime_plays_once_per_wake_before_recording_and_audio_is_flushed() -> None:
    events: list[str] = []
    source = RecordingSource([LOUD_FRAME, LOUD_FRAME, SILENT_FRAME, SILENT_FRAME])
    detector = RecordingDetector([0.9])
    listener = VoiceListener(
        audio_source=source,
        wake_word_detector=detector,
        transcriber=lambda audio: events.append("transcribed") or "open obs",
        on_command=lambda text: events.append(f"command:{text}"),
        on_wake=lambda: events.append("chime"),
        config=VoiceListenerConfig(silence_seconds=0.16),
    )
    listener.run_once()
    assert events == ["chime", "transcribed", "command:open obs"]
    assert source.drains == 2 and detector.resets == 2  # after the chime, and after the command


def test_waits_for_slow_talkers_instead_of_ending_on_leading_silence() -> None:
    commands: list[str] = []
    frames = [LOUD_FRAME] + [SILENT_FRAME] * 5 + [LOUD_FRAME, LOUD_FRAME] + [SILENT_FRAME] * 3
    listener = VoiceListener(
        audio_source=FakeAudioSource(frames),
        wake_word_detector=FakeWakeWordDetector([0.9]),
        transcriber=lambda audio: "turn it up",
        on_command=commands.append,
        config=VoiceListenerConfig(silence_seconds=0.16, no_speech_timeout_seconds=1.0),
    )
    listener.run_once()
    assert commands == ["turn it up"]


def test_gives_up_quietly_if_nothing_is_said_after_the_wake_word() -> None:
    transcribed: list[int] = []
    listener = VoiceListener(
        audio_source=FakeAudioSource([LOUD_FRAME] + [SILENT_FRAME] * 4),
        wake_word_detector=FakeWakeWordDetector([0.9]),
        transcriber=lambda audio: transcribed.append(1) or "x",
        on_command=lambda text: transcribed.append(2),
        config=VoiceListenerConfig(no_speech_timeout_seconds=0.24),
    )
    listener.run_once()
    assert transcribed == []


def test_a_transcript_of_only_the_wake_phrase_is_not_sent_to_fig() -> None:
    commands: list[str] = []
    listener = VoiceListener(
        audio_source=FakeAudioSource([LOUD_FRAME, LOUD_FRAME, SILENT_FRAME, SILENT_FRAME]),
        wake_word_detector=FakeWakeWordDetector([0.9]),
        transcriber=lambda audio: "Hey, Jarvis.",
        on_command=commands.append,
        config=VoiceListenerConfig(silence_seconds=0.16),
    )
    listener.run_once()
    assert commands == []
    assert is_only_wake_phrase("Hey Jarvis", VoiceListenerConfig().wake_phrase_words)
    assert not is_only_wake_phrase("hey jarvis open obs", VoiceListenerConfig().wake_phrase_words)


def test_cooldown_blocks_an_immediate_second_trigger() -> None:
    commands: list[str] = []
    now = {"t": 100.0}
    frames = [LOUD_FRAME, LOUD_FRAME, SILENT_FRAME, SILENT_FRAME, LOUD_FRAME, LOUD_FRAME, LOUD_FRAME, SILENT_FRAME, SILENT_FRAME]
    listener = VoiceListener(
        audio_source=FakeAudioSource(frames),
        wake_word_detector=FakeWakeWordDetector([0.9, 0.9, 0.9]),
        transcriber=lambda audio: "again",
        on_command=commands.append,
        config=VoiceListenerConfig(silence_seconds=0.16, cooldown_seconds=2.0),
        clock=lambda: now["t"],
    )
    listener.run_once()  # first command
    listener.run_once()  # frame heard during cooldown: ignored
    assert commands == ["again"]
    now["t"] += 3.0
    listener.run_once()  # cooldown over: triggers again
    assert commands == ["again", "again"]


def test_windows_chime_beeps_twice_and_survives_a_missing_sound_device(monkeypatch) -> None:
    import sys
    import types

    from figai.voice import chime

    beeps: list[tuple[int, int]] = []
    fake = types.SimpleNamespace(Beep=lambda freq, ms: beeps.append((freq, ms)))
    monkeypatch.setitem(sys.modules, "winsound", fake)
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(chime.time, "sleep", lambda _s: None)
    chime.play_listening_chime()
    assert len(beeps) == 2 and beeps[0][0] < beeps[1][0]  # rising two-note "bing"

    def broken(freq, ms):
        raise RuntimeError("no sound device")

    monkeypatch.setitem(sys.modules, "winsound", types.SimpleNamespace(Beep=broken))
    chime.play_listening_chime()  # must not raise


def test_wake_phrase_words_include_the_model_name() -> None:
    from figai.frontends.voice import wake_phrase_words

    assert "mycroft" in wake_phrase_words("hey_mycroft")
    assert "computer" in wake_phrase_words(r"C:\models\hey_computer.onnx")
