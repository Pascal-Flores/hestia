"""Smoke test the STT HTTP adapter without installing Whisper model weights."""

import importlib.util
from pathlib import Path
import sys
import types

from fastapi.testclient import TestClient


def test_stt_app(monkeypatch):
    monkeypatch.delenv("STT_MODEL", raising=False)
    loaded_models = []

    class Segment:
        text = " Bonjour "

    class WhisperStub:
        def __init__(self, model_name, **kwargs):
            loaded_models.append((model_name, kwargs))

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
        assert loaded_models == [("base", {"device": "cpu", "compute_type": "int8", "cpu_threads": 2})]
        assert client.get("/health").status_code == 200
        resp = client.post("/v1/transcribe", files={"file": ("hello.wav", b"raw", "audio/wav")})
        assert resp.json() == {"text": "Bonjour"}
        assert client.post("/v1/transcribe", files={"file": ("bad.bin", b"abc")}).status_code == 415


def test_stt_model_can_be_overridden(monkeypatch):
    from pathlib import Path
    import importlib.util
    import sys
    import types
    chosen = []

    class Stub:
        def __init__(self, name, **kwargs):
            chosen.append(name)

    monkeypatch.setenv("STT_MODEL", "small")
    monkeypatch.setitem(sys.modules, "faster_whisper", types.SimpleNamespace(WhisperModel=Stub))
    file = Path(__file__).resolve().parents[1] / "services" / "stt" / "app.py"
    spec = importlib.util.spec_from_file_location("hestia_stt_model_override_test", file)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    with TestClient(module.app):
        assert chosen == ["small"]

