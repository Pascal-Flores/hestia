"""Deterministic utterance capture after wake word detection."""

from __future__ import annotations

from dataclasses import dataclass
import io
import wave

import numpy as np

from .dsp import OUTPUT_RATE, WAKE_SAMPLES


def wav_bytes(pcm: np.ndarray, sample_rate: int = OUTPUT_RATE) -> bytes:
    if pcm.dtype != np.int16:
        raise ValueError("WAV input must be int16")
    output = io.BytesIO()
    with wave.open(output, "wb") as writer:
        writer.setnchannels(1)
        writer.setsampwidth(2)
        writer.setframerate(sample_rate)
        writer.writeframes(pcm.tobytes())
    return output.getvalue()


@dataclass(frozen=True)
class CaptureSettings:
    min_voice_seconds: float = 0.20
    silence_seconds: float = 0.85
    initial_timeout_seconds: float = 3.5
    max_seconds: float = 12.0
    rms_threshold: float = 0.008
    rnnoise_speech_threshold: float = 0.30

    def __post_init__(self):
        if (self.min_voice_seconds <= 0 or self.silence_seconds <= 0
                or self.initial_timeout_seconds <= 0 or self.max_seconds < self.initial_timeout_seconds
                or not 0 < self.rms_threshold < 1
                or not 0 <= self.rnnoise_speech_threshold <= 1):
            raise ValueError("Invalid capture settings")


class UtteranceCapture:
    """Collect denoised frames until enough speech and then trailing silence.

    Returns (recording|complete|timeout, WAV bytes or None). A short pre-roll
    avoids dropping the first consonant after the wake-word prediction.
    """

    def __init__(self, settings: CaptureSettings, preroll: list[np.ndarray] | None = None):
        self.settings = settings
        self.frames = [f.copy() for f in (preroll or [])]
        self.elapsed = 0.0
        self.voiced = 0.0
        self.trailing_silence = 0.0
        self.started = False

    def feed(self, pcm: np.ndarray, speech_probability: float | None) -> tuple[str, bytes | None]:
        if pcm.dtype != np.int16 or len(pcm) != WAKE_SAMPLES:
            raise ValueError("Expected 80 ms of 16 kHz mono int16 audio")
        duration = len(pcm) / OUTPUT_RATE
        self.elapsed += duration
        self.frames.append(pcm.copy())
        rms = float(np.sqrt(np.mean(np.square(pcm.astype(np.float32) / 32768.0))))
        # Speech confidence rejects background music more reliably than energy alone,
        # though RNNoise is not a full music/source separator.
        active = rms >= self.settings.rms_threshold and (
            speech_probability is None or speech_probability >= self.settings.rnnoise_speech_threshold
        )
        if active:
            self.started = True
            self.voiced += duration
            self.trailing_silence = 0.0
        elif self.started:
            self.trailing_silence += duration
        if self.started and self.voiced >= self.settings.min_voice_seconds and (
            self.trailing_silence >= self.settings.silence_seconds or self.elapsed >= self.settings.max_seconds
        ):
            return "complete", wav_bytes(np.concatenate(self.frames))
        if self.elapsed >= self.settings.max_seconds or (
            not self.started and self.elapsed >= self.settings.initial_timeout_seconds
        ):
            return "timeout", None
        return "recording", None
