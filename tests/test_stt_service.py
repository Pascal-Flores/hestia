"""Smoke test the STT HTTP adapter without installing Whisper model weights."""

import importlib.util
from pathlib import Path
import sys
import types

from fastapi.testclient import TestClient


def test_stt_app(monkeypatch):
    class Segment:
        text = " Bonjour "

    class WhisperStub:
        def __init__(self, *args, **kwargs):
            pass

        def transcribe(self, path, **kwargs):
            assert Path(path).is_file()
            assert kwargs["language"] == "fr"
            return [Segment()], None

    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=WhisperStub))
    file = Path(__file__).resolve().parents[1] / "services" / "stt" / "app.py"
    spec = importlib.util.spec_from_file_location("hestia_stt_test", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app) as client:
        assert client.get("/health").status_code == 200
        resp = client.post("/v1/transcribe", files={"file": ("hello.wav", b"raw", "audio/wav")})
        assert resp.json() == {"text": "Bonjour"}
        assert client.post("/v1/transcribe", files={"file": ("bad.bin", b"abc")}).status_code == 415
