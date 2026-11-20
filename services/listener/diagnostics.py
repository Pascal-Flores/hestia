"""Lightweight optional input level and wake model diagnostics.

Reports periodic *peaks of per-block RMS*, in dBFS, and best wake score.
Disabled by default to avoid continuous audio-level computation or noisy logs.
Audio is never stored by these metrics.
"""

from __future__ import annotations

import logging
import time

import numpy as np


def block_rms_dbfs(pcm: np.ndarray) -> float:
    """Return dBFS RMS for int16 audio (-120 dBFS for digital silence)."""
    if pcm.dtype != np.int16:
        raise ValueError("Wake diagnostic expects int16 PCM")
    if pcm.size == 0:
        return -120.0
    floats = pcm.astype(np.float32) / 32768.0
    rms = float(np.sqrt(np.mean(floats * floats)))
    return float(20 * np.log10(max(rms, 1e-6)))


class WakeDiagnostics:
    def __init__(self, *, threshold: float, report_seconds: float = 5.0,
                 logger: logging.Logger | None = None):
        if report_seconds <= 0:
            raise ValueError("LISTENER_DIAGNOSTICS_INTERVAL must be positive")
        self.threshold = threshold
        self.report_seconds = report_seconds
        self.logger = logger or logging.getLogger("hestia.listener")
        self.reset()

    def reset(self) -> None:
        self.started_at = time.monotonic()
        self.frames = 0
        self.wake_frames = 0
        self.peak_raw_rms = -120.0
        self.peak_clean_rms = -120.0
        self.peak_wake = 0.0

    def observe(self, *, raw: np.ndarray, clean: np.ndarray,
                wake_score: float | None = None) -> None:
        self.frames += 1
        self.peak_raw_rms = max(self.peak_raw_rms, block_rms_dbfs(raw))
        self.peak_clean_rms = max(self.peak_clean_rms, block_rms_dbfs(clean))
        if wake_score is not None:
            self.wake_frames += 1
            self.peak_wake = max(self.peak_wake, float(wake_score))

    def report_if_due(self) -> None:
        if time.monotonic() - self.started_at < self.report_seconds:
            return
        if self.frames:
            self.logger.info(
                "Wake diagnostics: raw max RMS=%.1f dBFS; enhanced max RMS=%.1f dBFS; "
                "max wake score=%.3f (threshold=%.3f); wake frames=%d. "
                "Say the configured wake phrase during this interval.",
                self.peak_raw_rms, self.peak_clean_rms,
                self.peak_wake, self.threshold, self.wake_frames,
            )
        self.reset()
