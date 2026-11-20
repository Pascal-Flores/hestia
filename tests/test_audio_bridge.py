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


def test_whisper_cpp_backend_converts_to_16khz_wav(monkeypatch):
    """Native STT uses whisper.cpp's own /inference multipart protocol."""
    import subprocess
    calls = []

    def fake_ffmpeg(cmd, **kwargs):
        calls.append(("ffmpeg", cmd, kwargs))
        assert "-ar" in cmd and "16000" in cmd
        return subprocess.CompletedProcess(cmd, 0, stdout=b"RIFFconvertedWAVE")

    def fake_post(url, **kwargs):
        calls.append(("http", url, kwargs))
        assert url.endswith("/inference")
        assert kwargs["data"] == {"response_format": "json", "language": "fr"}
        assert kwargs["files"]["file"][1] == b"RIFFconvertedWAVE"
        return httpx.Response(200, json={"text": " Salut ! "}, request=httpx.Request("POST", url))

    monkeypatch.setattr("hestia.server.subprocess.run", fake_ffmpeg)
    monkeypatch.setattr("hestia.server.httpx.post", fake_post)
    bridge = AudioBridge("http://127.0.0.1:8001/inference", "http://127.0.0.1:8000/tts",
                         stt_backend="whisper_cpp")
    assert bridge.transcribe(b"some arbitrary input", filename="file.mp3") == "Salut !"
    assert [item[0] for item in calls] == ["ffmpeg", "http"]


def test_unknown_audio_backend_is_rejected():
    with pytest.raises(ValueError, match="STT backend"):
        AudioBridge("http://stt", "http://tts", stt_backend="unknown")


def test_audio_bridge_normalizes_only_the_tts_payload(monkeypatch):
    requests = []
    def fake_post(url, **kwargs):
        requests.append(kwargs["data"]["text"])
        return httpx.Response(200, content=b"RIFFtestWAVE", request=httpx.Request("POST", url))
    monkeypatch.setattr("hestia.server.httpx.post", fake_post)
    bridge = AudioBridge("http://stt", "http://tts/tts")
    assert bridge.synthesize("Il est 14h30 et il fait 20°C.") == b"RIFFtestWAVE"
    assert requests == ["Il est quatorze heures trente et il fait vingt degrés Celsius."]

    plain = AudioBridge("http://stt", "http://tts/tts", normalize_numbers=False)
    plain.synthesize("42")
    assert requests[-1] == "42"
