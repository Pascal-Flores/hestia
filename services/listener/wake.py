"""openWakeWord adapter; custom .onnx model can replace the test wake word."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from .dsp import WAKE_SAMPLES


class WakeDetector:
    def __init__(self, model: str, threshold: float = 0.55, consecutive: int = 1):
        if not 0 < threshold <= 1 or not 1 <= consecutive <= 10:
            raise ValueError("Invalid wake word threshold or consecutive count")
        import openwakeword
        from openwakeword.model import Model

        if model.endswith((".onnx", ".tflite")) and not Path(model).is_file():
            raise FileNotFoundError(
                f"Wake word model '{model}' not found. For now use 'hey jarvis', "
                "or mount a trained Hestia .onnx model."
            )
        if not model.endswith((".onnx", ".tflite")):
            openwakeword.utils.download_models()
        self.model = Model(wakeword_models=[model], inference_framework="onnx")
        self.threshold = threshold
        self.consecutive = consecutive
        self.positive = 0

    def predict(self, pcm16: np.ndarray) -> bool:
        if pcm16.dtype != np.int16 or len(pcm16) != WAKE_SAMPLES:
            raise ValueError("Wake word expects 80 ms of 16 kHz mono int16")
        scores = self.model.predict(pcm16)
        detected = bool(scores) and max(float(p) for p in scores.values()) >= self.threshold
        self.positive = self.positive + 1 if detected else 0
        return self.positive >= self.consecutive

    def reset(self) -> None:
        self.model.reset()
        self.positive = 0
