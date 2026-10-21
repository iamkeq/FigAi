"""Real wake-word detector backed by openWakeWord.

openWakeWord ships pretrained models for specific words ("hey_jarvis", "alexa",
"hey_mycroft", ...) -- there is no out-of-the-box "fig" model. Until a custom
model is trained (openWakeWord's training notebook generates synthetic data
and trains a small model from just the target phrase), `wake_word_model`
defaults to one of the bundled words as an immediately-working stand-in; once
you train a custom "hey fig" model, point `wake_word_model` at its file path."""

from __future__ import annotations

import os

import numpy as np


class OpenWakeWordDetector:
    def __init__(self, model_name_or_path: str = "hey_jarvis") -> None:
        from openwakeword.model import Model

        if not os.path.exists(model_name_or_path):
            from openwakeword.utils import download_models

            download_models(model_names=[model_name_or_path])

        self._model = Model(wakeword_models=[model_name_or_path], inference_framework="onnx")
        self._key = next(iter(self._model.models))

    def score(self, frame: np.ndarray) -> float:
        predictions = self._model.predict(frame)
        return float(predictions.get(self._key, 0.0))
