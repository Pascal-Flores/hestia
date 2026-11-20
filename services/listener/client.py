"""Talk to Hestia's existing audio-to-audio endpoint."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import io
import wave

import httpx
import numpy as np


@dataclass(frozen=True)
class HestiaAnswer:
    transcript: str
    text: str
    status: str
    wav: bytes


class HestiaClient:
    def __init__(self, endpoint: str, *, api_key: str = ""):
        self.endpoint = endpoint
        self.api_key = api_key

    def ask(self, wav: bytes) -> HestiaAnswer:
        headers = {"X-Hestia-Key": self.api_key} if self.api_key else {}
        response = httpx.post(
            self.endpoint,
            files={"file": ("voice.wav", wav, "audio/wav")},
            headers=headers,
            timeout=300,
        )
        response.raise_for_status()
        obj = response.json()
        audio = base64.b64decode(obj["audio_base64"], validate=True)
        if not audio.startswith(b"RIFF"):
            raise ValueError("Hestia did not return a WAV file")
        return HestiaAnswer(obj["transcript"], obj["text"], obj["status"], audio)


def decode_wav(wav: bytes) -> tuple[np.ndarray, int]:
    with wave.open(io.BytesIO(wav), "rb") as stream:
        if stream.getcomptype() != "NONE" or stream.getsampwidth() != 2:
            raise ValueError("Expected uncompressed 16-bit PCM WAV")
        channels, rate = stream.getnchannels(), stream.getframerate()
        if channels not in (1, 2) or rate <= 0:
            raise ValueError("Unsupported audio channels or sample rate")
        audio = np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2").copy()
        return audio.reshape(-1, channels), rate
