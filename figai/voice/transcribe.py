"""Real speech-to-text via a local faster-whisper model -- runs fully on-device,
no cloud call, consistent with how the rest of Fig keeps data local/private."""

from __future__ import annotations

import numpy as np


class FasterWhisperTranscriber:
    def __init__(self, model_size: str = "base.en", device: str = "cpu", compute_type: str = "int8") -> None:
        from faster_whisper import WhisperModel

        self._model = WhisperModel(model_size, device=device, compute_type=compute_type)

    def __call__(self, audio_int16: np.ndarray) -> str:
        audio_float32 = audio_int16.astype(np.float32) / 32768.0
        segments, _info = self._model.transcribe(audio_float32)
        return "".join(segment.text for segment in segments).strip()
