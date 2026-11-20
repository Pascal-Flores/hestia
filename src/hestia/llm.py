"""Minimal OpenAI-compatible chat client, including llama.cpp server."""

from __future__ import annotations

import json
import os
from typing import Protocol
from urllib import error, request


class LLM(Protocol):
    def complete(self, *, system: str, user: str, json_mode: bool = False) -> str: ...


class OpenAICompatibleLLM:
    def __init__(self, url: str = "http://127.0.0.1:8080/v1/chat/completions",
                 model: str = "local", api_key: str | None = None, timeout: int = 90):
        self.url, self.model = url, model
        self.api_key = api_key
        self.timeout = timeout

    @classmethod
    def from_env(cls, prefix: str = "HESTIA_LLM") -> "OpenAICompatibleLLM":
        return cls(
            url=os.getenv(f"{prefix}_URL", "http://127.0.0.1:8080/v1/chat/completions"),
            model=os.getenv(f"{prefix}_MODEL", "local"),
            api_key=os.getenv(f"{prefix}_API_KEY"),
        )

    def complete(self, *, system: str, user: str, json_mode: bool = False) -> str:
        payload: dict = {
            "model": self.model, "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        req = request.Request(self.url, data=json.dumps(payload).encode("utf-8"), headers=headers)
        try:
            with request.urlopen(req, timeout=self.timeout) as response:
                body = json.load(response)
        except (OSError, error.HTTPError, ValueError) as exc:
            raise RuntimeError(f"LLM request failed: {exc}") from exc
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("Malformed LLM response") from exc
        if not isinstance(content, str):
            raise RuntimeError("LLM did not return text")
        return content.strip()
