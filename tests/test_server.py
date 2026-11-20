"""HTTP tests without fetching model weights or contacting real services."""

import base64
from dataclasses import dataclass

from fastapi.testclient import TestClient
import pytest

from hestia.routing import Route
from hestia.runtime import Outcome
from hestia.server import create_app, MAX_AUDIO_BYTES


class FakeRouter:
    def route(self, text):
        return Route("matched", "math.add", (("math.add", 0.94),))


@dataclass
class FakeTool:
    id: str = "math.add"
    name: str = "Addition"
    description: str = "Somme"
    examples: tuple = ("Additionne",)


class FakeEngine:
    tools = {"math.add": FakeTool()}
    router = FakeRouter()

    def handle(self, text, *, confirmed=False, context=None):
        assert text
        return Outcome("completed", "C'est fait.", tool_id="math.add",
                       route=self.router.route(text), trace=[{"step": "execute"}])


class FakeAudio:
    def transcribe(self, audio, filename="speech.wav"):
        assert audio and filename
        return "Combien font 2 plus 2 ?"

    def synthesize(self, text):
        assert text
        return b"RIFF----WAVEdata"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("HESTIA_API_KEY", raising=False)
    with TestClient(create_app(engine=FakeEngine(), audio=FakeAudio())) as test:
        yield test


def test_api_routes_and_tools(client):
    assert client.get("/health/live").json() == {"status": "ok"}
    assert client.get("/v1/tools").json()[0]["id"] == "math.add"
    selection = client.post("/v1/route", json={"text": "Additionne"}).json()
    assert selection["tool_id"] == "math.add"
    response = client.post("/v1/chat", json={"text": "Combien font 2 plus 2 ?"}).json()
    assert response["status"] == "completed"
    assert response["text"] == "C'est fait."
    assert response["candidates"][0]["score"] == 0.94
    assert "trace" not in response
    assert "trace" in client.post("/v1/chat", json={"text": "x", "debug": True}).json()
    assert client.post("/v1/chat", json={"text": ""}).status_code == 422


def test_audio_services(client):
    wav = ("question.wav", b"RIFFfakeWAVE", "audio/wav")
    transcribed = client.post("/v1/audio/transcribe", files={"file": wav})
    assert transcribed.json()["text"] == "Combien font 2 plus 2 ?"
    spoken = client.post("/v1/audio/speak", json={"text": "bonjour"})
    assert spoken.status_code == 200
    assert spoken.headers["content-type"] == "audio/wav"
    assert spoken.content.startswith(b"RIFF")
    completed = client.post("/v1/audio/ask", files={"file": wav}).json()
    assert completed["transcript"] == "Combien font 2 plus 2 ?"
    assert base64.b64decode(completed["audio_base64"]).startswith(b"RIFF")


def test_audio_size_limit(client):
    response = client.post("/v1/audio/transcribe", files={"file": ("a.wav", b"", "audio/wav")})
    assert response.status_code == 413
    response = client.post("/v1/audio/transcribe", files={"file": ("a.wav", b"a" * (MAX_AUDIO_BYTES + 1), "audio/wav")})
    assert response.status_code == 413


def test_api_key_and_health(client, monkeypatch):
    monkeypatch.setenv("HESTIA_API_KEY", "my-test-key")
    assert client.post("/v1/chat", json={"text": "x"}).status_code == 401
    ok = client.post("/v1/chat", json={"text": "x"}, headers={"X-Hestia-Key": "my-test-key"})
    assert ok.status_code == 200
    assert client.post("/v1/tools/reload", headers={"X-Hestia-Key": "my-test-key"}).status_code == 409
    assert client.get("/health/live").status_code == 200


def test_readiness(monkeypatch, client):
    class Healthy:
        status_code = 200
    monkeypatch.setattr("hestia.server.httpx.get", lambda *args, **kwargs: Healthy())
    resp = client.get("/health/ready")
    assert resp.status_code == 200
    assert resp.json()["services"] == {"llm": True, "stt": True, "tts": True}

    class Unhealthy:
        status_code = 503
    monkeypatch.setattr("hestia.server.httpx.get", lambda *args, **kwargs: Unhealthy())
    resp = client.get("/health/ready")
    assert resp.status_code == 503
    assert resp.json()["ready"] is False
