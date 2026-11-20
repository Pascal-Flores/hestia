"""Hardware-compatible TTS playback for direct ALSA/PortAudio devices.

Pocket TTS can produce WAV at a rate the selected ALSA PCM device rejects.
Probe supported hardware rates before playback and resample to a valid one.
SoXR is the preferred high-quality converter; NumPy is a small fallback for
non-Docker development/test environments.
"""

from __future__ import annotations

import logging

import numpy as np

from .client import decode_wav


def select_playback_rate(sd, *, device, channels: int, source_rate: int,
                         requested_rate: int | None = None) -> tuple[int, dict]:
    """Return a validated playback sample rate and the chosen output device."""
    info = sd.query_devices(device, kind="output")
    if int(info["max_output_channels"]) < channels:
        raise RuntimeError(
            f"Output device {info['name']!r} supports only "
            f"{info['max_output_channels']} channels (audio has {channels})"
        )
    if requested_rate is not None and requested_rate <= 0:
        raise ValueError("LISTENER_OUTPUT_RATE must be a positive integer")
    attempts: list[int] = []
    if requested_rate is not None:
        candidates = [requested_rate]
    else:
        candidates = [source_rate, int(round(float(info["default_samplerate"]))),
                      48_000, 44_100, 32_000, 16_000]
    for rate in candidates:
        if rate <= 0 or rate in attempts:
            continue
        attempts.append(rate)
        try:
            sd.check_output_settings(device=device, samplerate=rate, channels=channels,
                                     dtype="int16")
            return rate, info
        except sd.PortAudioError:
            continue
    raise RuntimeError(
        f"No supported sample rate for output device {info['name']!r} "
        f"with {channels} channel(s); tested {attempts}. "
        "Check LISTENER_OUTPUT_DEVICE, or set LISTENER_OUTPUT_RATE=48000."
    )


def resample_pcm16(samples: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Resample mono/stereo int16 samples without changing pitch or duration."""
    if samples.dtype != np.int16 or samples.ndim != 2 or samples.shape[1] not in (1, 2):
        raise ValueError("Expected 2D mono/stereo int16 PCM")
    if source_rate <= 0 or target_rate <= 0:
        raise ValueError("Sample rates must be positive")
    if source_rate == target_rate or samples.shape[0] == 0:
        return samples
    try:
        import soxr
    except ImportError:
        # Works without an extra dependency for local development;
        # the Docker image installs SoXR to preserve full audio fidelity.
        count = max(1, round(samples.shape[0] * target_rate / source_rate))
        src = np.arange(samples.shape[0], dtype=np.float64)
        dst = np.arange(count, dtype=np.float64) * (source_rate / target_rate)
        result = np.stack([
            np.interp(dst, src, samples[:, channel]).astype(np.float32)
            for channel in range(samples.shape[1])
        ], axis=1)
        return np.clip(np.rint(result), -32768, 32767).astype(np.int16)
    return soxr.resample(samples, source_rate, target_rate, quality="HQ")


def play_wav(wav: bytes, *, device=None, requested_rate: int | None = None,
             sd=None, logger: logging.Logger | None = None) -> tuple[int, int]:
    """Play a WAV after choosing a supported hardware rate. Return original/target."""
    if sd is None:
        import sounddevice as sd
    log = logger or logging.getLogger("hestia.listener")
    samples, source_rate = decode_wav(wav)
    target_rate, info = select_playback_rate(
        sd, device=device, channels=samples.shape[1],
        source_rate=source_rate, requested_rate=requested_rate,
    )
    if source_rate != target_rate:
        log.info("Resampling Pocket TTS output: %d -> %d Hz for %s",
                 source_rate, target_rate, info["name"])
        samples = resample_pcm16(samples, source_rate, target_rate)
    log.info("Playing response: %.2f s, %d Hz, %d channel(s), device=%s",
             len(samples) / target_rate, target_rate, samples.shape[1], info["name"])
    sd.play(samples, samplerate=target_rate, device=device, blocking=True)
    return source_rate, target_rate
