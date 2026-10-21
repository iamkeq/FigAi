"""Real microphone capture via sounddevice, yielding fixed-size int16 frames."""

from __future__ import annotations

import queue

import numpy as np
import sounddevice as sd

from figai.voice.listener import FRAME_SIZE, SAMPLE_RATE


class MicrophoneAudioSource:
    def __init__(self, device: int | str | None = None) -> None:
        self._queue: queue.Queue[np.ndarray] = queue.Queue()
        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="int16",
            blocksize=FRAME_SIZE,
            device=device,
            callback=self._on_audio,
        )
        self._stream.start()

    def _on_audio(self, indata: np.ndarray, frames: int, time_info: object, status: object) -> None:
        self._queue.put(indata[:, 0].copy())

    def read_frame(self) -> np.ndarray:
        return self._queue.get()

    def close(self) -> None:
        self._stream.stop()
        self._stream.close()
