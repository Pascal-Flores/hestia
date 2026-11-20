import httpx
import pytest

from hestia.server import AudioBridge


def test_audio_bridge_calls_persistent_services(monkeypatch):
    requests = []

    def fake_post(url, **kwargs):
        requests.append((url, kwargs))
        if url.endswith("/v1/transcribe"):
            return httpx.Response(200, json={"text": " Bonjour. "}, request=httpx.Request("POST", url))
        return httpx.Response(200, content=b"RIFFtestWAVE", request=httpx.Request("POST", url))

    monkeypatch.setattr("hestia.server.httpx.post", fake_post)
    audio = AudioBridge("http://stt:8001/v1/transcribe", "http://tts:8000/tts")
    assert audio.transcribe(b"RIFFrawWAVE") == "Bonjour."
    assert audio.synthesize("Salut") == b"RIFFtestWAVE"
    assert "files" in requests[0][1]
    assert requests[1][1]["data"] == {"text": "Salut"}


def test_audio_bridge_rejects_invalid_wav(monkeypatch):
    url = "http://tts:8000/tts"
    monkeypatch.setattr("hestia.server.httpx.post", lambda *a, **kw: httpx.Response(
        200, content=b"not a wav", request=httpx.Request("POST", url)))
    with pytest.raises(RuntimeError, match="WAV"):
        AudioBridge("http://stt", url).synthesize("Bonjour")
