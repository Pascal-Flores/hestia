"""Hardware-independent checks for playback sample-rate adaptation."""

import io
import logging
from pathlib import Path
import sys
import wave

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.listener.playback import (
    play_wav, resample_pcm16, select_playback_rate,
)
from services.listener.client import decode_wav


class FakeSoundDevice:
    class PortAudioError(Exception):
        pass

    def __init__(self, rates=(48000,), name="CX11970 Analog"):
        self.rates = set(rates)
        self.info = {
            "name": name, "max_output_channels": 2,
            "default_samplerate": 48000.0,
        }
        self.attempts = []
        self.played = None

    def query_devices(self, device, *, kind):
        assert kind == "output"
        return self.info

    def check_output_settings(self, *, device, samplerate, channels, dtype):
        assert dtype == "int16"
        self.attempts.append(samplerate)
        if samplerate not in self.rates:
            raise self.PortAudioError("Invalid sample rate")

    def play(self, samples, *, samplerate, device, blocking):
        self.played = (samples, samplerate, device, blocking)


def wav_bytes(samples: np.ndarray, rate: int) -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as output:
        output.setnchannels(samples.shape[1])
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(samples.astype("<i2").tobytes())
    return buf.getvalue()


def test_rate_selection_tries_source_before_device_default():
    sd = FakeSoundDevice(rates={24000, 48000})
    rate, info = select_playback_rate(sd, device=1, channels=1, source_rate=24000)
    assert rate == 24000
    assert sd.attempts == [24000]
    assert info["name"] == "CX11970 Analog"


def test_rate_selection_falls_back_to_supported_48khz():
    sd = FakeSoundDevice(rates={48000})
    rate, _ = select_playback_rate(sd, device=1, channels=1, source_rate=24000)
    assert rate == 48000
    assert sd.attempts == [24000, 48000]


def test_rate_override_is_validated():
    sd = FakeSoundDevice(rates={48000})
    with pytest.raises(RuntimeError, match="No supported sample rate"):
        select_playback_rate(sd, device=1, channels=1,
                             source_rate=24000, requested_rate=44100)
    assert sd.attempts == [44100]
    with pytest.raises(ValueError, match="OUTPUT_RATE"):
        select_playback_rate(sd, device=1, channels=1,
                             source_rate=24000, requested_rate=0)


def test_play_wav_resamples_to_device_and_preserves_duration(caplog):
    sd = FakeSoundDevice()
    original = np.full((24000, 1), 6000, np.int16)
    with caplog.at_level(logging.INFO):
        src, dst = play_wav(wav_bytes(original, 24000), device=1, sd=sd)
    samples, rate, device, blocking = sd.played
    assert (src, dst) == (24000, 48000)
    assert (rate, device, blocking) == (48000, 1, True)
    assert samples.dtype == np.int16
    assert samples.shape == (48000, 1)
    assert 5500 < samples[3000:45000].mean() < 6500
    assert "Resampling Pocket TTS" in caplog.text


def test_stereo_resampling_and_same_rate_noop():
    stereo = np.full((2400, 2), [3000, -3000], np.int16)
    converted = resample_pcm16(stereo, 24000, 48000)
    assert converted.shape == (4800, 2)
    assert converted.dtype == np.int16
    assert resample_pcm16(stereo, 24000, 24000) is stereo
    assert abs(int(converted[2000, 0]) - 3000) < 150
    assert abs(int(converted[2000, 1]) + 3000) < 150


def test_device_channel_mismatch_is_reported():
    sd = FakeSoundDevice()
    sd.info["max_output_channels"] = 1
    with pytest.raises(RuntimeError, match="supports only 1 channels"):
        select_playback_rate(sd, device=1, channels=2, source_rate=24000)


def test_wav_decoded_at_original_sample_rate():
    payload = wav_bytes(np.zeros((240, 1), np.int16), 24000)
    samples, rate = decode_wav(payload)
    assert (samples.shape, rate) == ((240, 1), 24000)
