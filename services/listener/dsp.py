"""Streaming 48 kHz mono microphone enhancement, shared by wake word and STT.

RNNoise is a speech enhancer, *not* a guaranteed vocal/music separator.
The built-in model operates on 480 samples (10 ms) at 48 kHz.
"""

from __future__ import annotations

import ctypes
import ctypes.util
from dataclasses import dataclass

import numpy as np

INPUT_RATE = 48_000
OUTPUT_RATE = 16_000
BLOCK_SAMPLES = 3_840  # 80 ms, also 8 RNNoise frames
RNNOISE_FRAME = 480
WAKE_SAMPLES = 1_280


class StreamingDecimator:
    """Stateful anti-aliasing FIR followed by exact 48 kHz -> 16 kHz decimation.

    Retains the previous 62 samples to avoid discontinuities at block edges.
    A 63-tap Hamming-windowed low-pass works without a scipy dependency.
    """

    def __init__(self):
        count = 63
        n = np.arange(count, dtype=np.float64) - (count - 1) / 2
        cutoff = 7_200 / INPUT_RATE
        weights = 2 * cutoff * np.sinc(2 * cutoff * n) * np.hamming(count)
        self.weights = (weights / weights.sum()).astype(np.float32)
        self.history = np.zeros(count - 1, dtype=np.float32)

    def process(self, pcm: np.ndarray) -> np.ndarray:
        if pcm.dtype != np.int16 or len(pcm) % 3:
            raise ValueError("Resampler requires int16 samples in multiples of three")
        continuous = np.concatenate((self.history, pcm.astype(np.float32)))
        filtered = np.convolve(continuous, self.weights, mode="valid")
        self.history = continuous[-len(self.history):].copy()
        return np.clip(np.rint(filtered[::3]), -32768, 32767).astype(np.int16)


@dataclass
class ProcessedAudio:
    pcm16: np.ndarray
    speech_probability: float | None


class RNNoise:
    """Use Xiph RNNoise's small native shared library through ctypes."""

    def __init__(self, library_path: str | None = None):
        name = library_path or ctypes.util.find_library("rnnoise") or "librnnoise.so.0"
        try:
            lib = ctypes.CDLL(name)
        except OSError as exc:
            raise RuntimeError("RNNoise unavailable; rebuild listener or set LISTENER_FILTER=none") from exc
        lib.rnnoise_create.argtypes = [ctypes.c_void_p]
        lib.rnnoise_create.restype = ctypes.c_void_p
        lib.rnnoise_get_frame_size.argtypes = []
        lib.rnnoise_get_frame_size.restype = ctypes.c_int
        lib.rnnoise_process_frame.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_float), ctypes.POINTER(ctypes.c_float)]
        lib.rnnoise_process_frame.restype = ctypes.c_float
        lib.rnnoise_destroy.argtypes = [ctypes.c_void_p]
        lib.rnnoise_destroy.restype = None
        if lib.rnnoise_get_frame_size() != RNNOISE_FRAME:
            raise RuntimeError("Unexpected RNNoise frame size")
        state = lib.rnnoise_create(None)
        if not state:
            raise RuntimeError("RNNoise initialization failed")
        self.lib = lib
        self.state = state

    def process(self, pcm: np.ndarray) -> tuple[np.ndarray, float]:
        if pcm.dtype != np.int16 or len(pcm) % RNNOISE_FRAME:
            raise ValueError("RNNoise expects int16 48 kHz in 480-sample frames")
        incoming = pcm.astype(np.float32)
        outgoing = np.empty_like(incoming)
        probabilities = []
        for start in range(0, len(pcm), RNNOISE_FRAME):
            source = incoming[start:start + RNNOISE_FRAME]
            target = outgoing[start:start + RNNOISE_FRAME]
            score = self.lib.rnnoise_process_frame(
                self.state,
                target.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
                source.ctypes.data_as(ctypes.POINTER(ctypes.c_float)),
            )
            probabilities.append(float(score))
        return np.clip(np.rint(outgoing), -32768, 32767).astype(np.int16), max(probabilities)

    def close(self) -> None:
        if self.state:
            self.lib.rnnoise_destroy(self.state)
            self.state = None


class AudioPreprocessor:
    """Filter once, then pass the *same* enhanced 16 kHz PCM to wake and STT."""

    def __init__(self, mode: str = "rnnoise", *, denoiser=None):
        if mode not in ("rnnoise", "none"):
            raise ValueError(f"Unknown LISTENER_FILTER: {mode}")
        self.mode = mode
        self.denoiser = denoiser if denoiser is not None else (RNNoise() if mode == "rnnoise" else None)
        self.resampler = StreamingDecimator()

    def process(self, pcm48: np.ndarray) -> ProcessedAudio:
        if pcm48.dtype != np.int16 or len(pcm48) != BLOCK_SAMPLES:
            raise ValueError(f"Input must be {BLOCK_SAMPLES} mono int16 samples at 48 kHz")
        clean, score = self.denoiser.process(pcm48) if self.denoiser else (pcm48, None)
        return ProcessedAudio(pcm16=self.resampler.process(clean), speech_probability=score)

    def close(self) -> None:
        if self.denoiser:
            self.denoiser.close()
