"""Pure unit tests; do not need PortAudio, Docker, or downloaded ML models."""

import base64
import io
from pathlib import Path
import sys
import types
import wave

import httpx
import numpy as np
import pytest
import yaml

# The listener is deliberately not installed into the Hestia API package.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.listener.client import HestiaClient, decode_wav
from services.listener.dsp import (
    AudioPreprocessor, BLOCK_SAMPLES, RNNOISE_FRAME, StreamingDecimator,
    WAKE_SAMPLES,
)
from services.listener.session import CaptureSettings, UtteranceCapture, wav_bytes
from services.listener.wake import WakeDetector


def test_resampler_retains_state_across_blocks():
    signal = (np.sin(np.arange(BLOCK_SAMPLES * 3) / 7) * 12_000).astype(np.int16)
    a = StreamingDecimator()
    first = np.concatenate([a.process(signal[i:i + BLOCK_SAMPLES])
                            for i in range(0, len(signal), BLOCK_SAMPLES)])
    b = StreamingDecimator()
    second = b.process(signal)
    assert len(first) == WAKE_SAMPLES * 3
    np.testing.assert_array_equal(first, second)


def test_audio_filter_is_shared_by_wake_and_stt():
    class FakeRNNoise:
        def __init__(self):
            self.calls = 0
            self.closed = False

        def process(self, frame):
            self.calls += 1
            return (frame // 2).astype(np.int16), 0.87

        def close(self):
            self.closed = True

    denoise = FakeRNNoise()
    processor = AudioPreprocessor("rnnoise", denoiser=denoise)
    sample = np.full(BLOCK_SAMPLES, 3000, dtype=np.int16)
    output = processor.process(sample)
    assert denoise.calls == 1
    assert output.speech_probability == 0.87
    assert output.pcm16.dtype == np.int16
    assert len(output.pcm16) == WAKE_SAMPLES
    assert 1100 < int(np.mean(output.pcm16)) < 1700
    processor.close()
    assert denoise.closed


def test_unfiltered_audio_works_without_rnnoise():
    sample = np.zeros(BLOCK_SAMPLES, dtype=np.int16)
    processed = AudioPreprocessor("none").process(sample)
    assert processed.speech_probability is None
    assert len(processed.pcm16) == WAKE_SAMPLES
    with pytest.raises(ValueError):
        AudioPreprocessor("music-magic")


def test_capture_stops_after_speech_and_trailing_silence():
    capture = UtteranceCapture(CaptureSettings(), preroll=[np.zeros(WAKE_SAMPLES, np.int16)])
    speech = np.full(WAKE_SAMPLES, 6500, np.int16)
    silence = np.zeros(WAKE_SAMPLES, np.int16)
    for _ in range(4):
        assert capture.feed(speech, 0.9)[0] == "recording"
    status, out = "recording", None
    for _ in range(15):
        status, out = capture.feed(silence, 0.0)
        if status == "complete":
            break
    assert status == "complete"
    with wave.open(io.BytesIO(out), "rb") as wav:
        assert wav.getframerate() == 16000
        assert wav.getnchannels() == 1
        assert wav.getnframes() >= WAKE_SAMPLES * 5


def test_capture_rejects_music_without_speech_confidence():
    settings = CaptureSettings(initial_timeout_seconds=0.32)
    capture = UtteranceCapture(settings)
    music = np.full(WAKE_SAMPLES, 12_000, np.int16)
    status = "recording"
    for _ in range(6):
        status, _ = capture.feed(music, 0.05)
        if status == "timeout":
            break
    assert status == "timeout"


def test_capture_without_filter_uses_energy_only():
    capture = UtteranceCapture(CaptureSettings())
    speech = np.full(WAKE_SAMPLES, 6000, np.int16)
    for _ in range(4):
        assert capture.feed(speech, None)[0] == "recording"
    for _ in range(15):
        status, content = capture.feed(np.zeros(WAKE_SAMPLES, np.int16), None)
        if status == "complete":
            assert content.startswith(b"RIFF")
            break
    else:
        pytest.fail("Did not end capture")


def test_hestia_client_audio_round_trip_and_key(monkeypatch):
    expected_wav = wav_bytes(np.zeros(1600, np.int16))
    def fake_post(url, **kwargs):
        assert url == "http://hestia:8765/v1/audio/ask"
        assert kwargs["headers"] == {"X-Hestia-Key": "testkey"}
        assert kwargs["files"]["file"][1] == expected_wav
        payload = {"transcript": "Quelle heure est-il ?", "text": "Il est midi.",
                   "status": "completed", "audio_base64": base64.b64encode(expected_wav).decode()}
        return httpx.Response(200, json=payload, request=httpx.Request("POST", url))
    monkeypatch.setattr("services.listener.client.httpx.post", fake_post)
    result = HestiaClient("http://hestia:8765/v1/audio/ask", api_key="testkey").ask(expected_wav)
    assert result.text == "Il est midi."
    samples, rate = decode_wav(result.wav)
    assert rate == 16000 and samples.shape == (1600, 1)


def test_wake_detector_works_with_builtin_or_custom(monkeypatch, tmp_path):
    class FakeModel:
        def __init__(self, wakeword_models, inference_framework):
            assert inference_framework == "onnx"
            assert len(wakeword_models) == 1
            self.queue = [0.1, 0.7, 0.8]
            self.reset_called = False

        def predict(self, frame):
            assert len(frame) == WAKE_SAMPLES
            return {"name_from_model": self.queue.pop(0)}

        def reset(self):
            self.reset_called = True

    downloaded = []
    fake_lib = types.ModuleType("openwakeword")
    fake_lib.utils = types.SimpleNamespace(download_models=lambda: downloaded.append(True))
    fake_model_module = types.ModuleType("openwakeword.model")
    fake_model_module.Model = FakeModel
    monkeypatch.setitem(sys.modules, "openwakeword", fake_lib)
    monkeypatch.setitem(sys.modules, "openwakeword.model", fake_model_module)
    detector = WakeDetector("hey jarvis", threshold=0.55, consecutive=2)
    pcm = np.zeros(WAKE_SAMPLES, dtype=np.int16)
    assert not detector.predict(pcm)
    assert not detector.predict(pcm)
    assert detector.predict(pcm)
    assert downloaded
    assert detector.last_score == 0.8
    assert detector.last_label == "name_from_model"
    detector.reset()
    assert detector.last_score == 0.0
    assert detector.positive == 0 and detector.model.reset_called
    with pytest.raises(FileNotFoundError):
        WakeDetector(str(tmp_path / "missing.onnx"))


def test_compose_listener_is_enabled_by_default():
    project = Path(__file__).resolve().parents[1]
    config = yaml.safe_load((project / "compose.yaml").read_text())
    listener = config["services"]["listener"]
    assert "profiles" not in listener
    assert listener["build"]["dockerfile"] == "docker/Dockerfile.listener"
    assert config["services"]["hestia"]["build"]["dockerfile"] == "docker/Dockerfile.core-rust"
    assert config["services"]["hestia"]["healthcheck"]["test"][1] == "curl"
    assert listener["environment"]["LISTENER_FILTER"]
    assert listener["environment"]["LISTENER_CONTROL_PORT"]
    assert config["services"]["hestia"]["environment"]["HESTIA_LISTENER_CONTROL_URL"].startswith("http://listener:")
    assert "/dev/snd:/dev/snd" in listener["devices"]
    assert "llm" in config["services"] and "stt" in config["services"]


import logging

from services.listener.diagnostics import WakeDiagnostics, block_rms_dbfs


def test_wake_diagnostics_reports_audio_levels_and_peak_score(caplog, monkeypatch):
    import services.listener.diagnostics as diag
    now = [0.0]
    monkeypatch.setattr(diag.time, "monotonic", lambda: now[0])
    stats = WakeDiagnostics(threshold=0.55, report_seconds=1.0)
    stats.observe(
        raw=np.full(3840, 10000, np.int16),
        clean=np.full(1280, 7000, np.int16),
        wake_score=0.43,
    )
    now[0] = 1.1
    with caplog.at_level(logging.INFO, logger="hestia.listener"):
        stats.report_if_due()
    assert "Wake diagnostics" in caplog.text
    assert "max wake score=0.430" in caplog.text
    assert "threshold=0.550" in caplog.text
    assert stats.frames == 0
    assert block_rms_dbfs(np.zeros(1280, np.int16)) == -120.0


def test_test_once_excludes_wake_word(monkeypatch):
    from services.listener import app
    received = []
    monkeypatch.setattr(app, "run", lambda **options: received.append(options))
    app.main(["--test-once", "6"])
    assert received == [{"record_sample": 0.0, "test_once": 6.0}]
