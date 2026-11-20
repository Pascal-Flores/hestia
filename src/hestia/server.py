"""HTTP gateway for Hestia: local semantic workflows + remote model services.

Audio services and the LLM stay inside Docker's private network. The API is
intended for trusted local users; set HESTIA_API_KEY before LAN exposure.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
import json
from pathlib import Path
import secrets
import subprocess
import tempfile
from typing import Any

from fastapi import Depends, FastAPI, File, Header, HTTPException, UploadFile
from fastapi.responses import Response
import httpx
from pydantic import BaseModel, Field

from .llm import OpenAICompatibleLLM
from .manifest import load_tools
from .routing import FastEmbedder, SemanticRouter, SentenceTransformerEmbedder
from .runtime import WorkflowEngine

MAX_AUDIO_BYTES = 10 * 1024 * 1024
MAX_TTS_CHARS = 2000


class TextRequest(BaseModel):
    text: str = Field(min_length=1, max_length=2000)


class ChatRequest(TextRequest):
    confirmed: bool = False
    context: dict[str, Any] = Field(default_factory=dict)
    debug: bool = False


class AudioBridge:
    """Calls the two persistent internal audio services over HTTP."""

    def __init__(self, stt_url: str, tts_url: str, *, profile: str = "natural"):
        self.stt_url = stt_url
        self.tts_url = tts_url
        if profile not in {"natural", "mii"}:
            raise ValueError("Unknown voice profile")
        self.profile = profile

    def transcribe(self, audio: bytes, filename: str = "speech.wav") -> str:
        try:
            response = httpx.post(
                self.stt_url, files={"file": (Path(filename).name, audio, "application/octet-stream")},
                timeout=180,
            )
            response.raise_for_status()
            text = response.json()["text"]
            if not isinstance(text, str):
                raise ValueError("Invalid STT response")
            return text.strip()
        except (httpx.HTTPError, ValueError, KeyError) as exc:
            raise RuntimeError(f"STT service unavailable: {exc}") from exc

    def synthesize(self, text: str) -> bytes:
        if not text.strip() or len(text) > MAX_TTS_CHARS:
            raise ValueError("Text for speech must contain 1-2000 characters")
        try:
            response = httpx.post(self.tts_url, data={"text": text}, timeout=240)
            response.raise_for_status()
            audio = response.content
            if not audio.startswith(b"RIFF"):
                raise ValueError("TTS did not return WAV audio")
        except (httpx.HTTPError, ValueError) as exc:
            raise RuntimeError(f"TTS service unavailable: {exc}") from exc
        if self.profile == "mii":
            with tempfile.TemporaryDirectory() as directory:
                source = Path(directory) / "raw.wav"
                dest = Path(directory) / "out.wav"
                source.write_bytes(audio)
                subprocess.run(["sox", str(source), str(dest), "pitch", "250",
                                "highpass", "180", "lowpass", "5000"],
                               check=True, timeout=45, capture_output=True)
                audio = dest.read_bytes()
        return audio


def _create_engine() -> WorkflowEngine:
    tools = load_tools(os.getenv("HESTIA_TOOLS_DIR", "/app/tools"))
    if not tools:
        raise RuntimeError("No tools installed; cannot start the router")
    model_name = os.getenv("HESTIA_EMBEDDING_MODEL", "intfloat/multilingual-e5-small")
    backend = os.getenv("HESTIA_EMBEDDING_BACKEND", "fastembed")
    if backend == "fastembed":
        embedder = FastEmbedder(model_name, cache_dir=os.getenv("HESTIA_EMBEDDING_CACHE"))
    elif backend == "sentence-transformers":
        embedder = SentenceTransformerEmbedder(model_name)
    else:
        raise ValueError(f"Unknown embedding backend: {backend}")
    router = SemanticRouter(tools, embedder,
                            min_score=float(os.getenv("HESTIA_ROUTE_MIN_SCORE", "0.80")),
                            min_margin=float(os.getenv("HESTIA_ROUTE_MIN_MARGIN", "0.035")))
    local_llm = OpenAICompatibleLLM.from_env("HESTIA_LLM")
    fallback = (OpenAICompatibleLLM.from_env("HESTIA_FALLBACK_LLM")
                if os.getenv("HESTIA_FALLBACK_LLM_URL") else None)
    permissions = frozenset(
        p.strip() for p in os.getenv("HESTIA_ALLOWED_PERMISSIONS", "").split(",") if p.strip()
    )
    return WorkflowEngine(tools, router, local_llm,
                          allowed_permissions=permissions, fallback_llm=fallback)


def _serialize(outcome, *, debug: bool = False) -> dict[str, Any]:
    response: dict[str, Any] = {
        "status": outcome.status, "text": outcome.text, "tool_id": outcome.tool_id,
        "candidates": ([{"tool_id": ident, "score": score}
                        for ident, score in outcome.route.candidates]
                       if outcome.route else []),
    }
    if debug:
        response["trace"] = outcome.trace
    return response


def create_app(*, engine: WorkflowEngine | None = None,
               audio: AudioBridge | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        app.state.engine = engine if engine is not None else _create_engine()
        app.state.audio = audio if audio is not None else AudioBridge(
            os.getenv("HESTIA_STT_URL", "http://stt:8001/v1/transcribe"),
            os.getenv("HESTIA_TTS_URL", "http://tts:8000/tts"),
            profile=os.getenv("HESTIA_VOICE_PROFILE", "natural"),
        )
        yield

    app = FastAPI(title="Hestia", version="0.2.0", lifespan=lifespan)

    def authorize(x_hestia_key: str | None = Header(default=None)) -> None:
        key = os.getenv("HESTIA_API_KEY", "")
        if key and (x_hestia_key is None or not secrets.compare_digest(key, x_hestia_key)):
            raise HTTPException(status_code=401, detail="Invalid Hestia key")

    def get_engine():
        return app.state.engine

    def get_audio():
        return app.state.audio

    @app.get("/health/live")
    def live():
        return {"status": "ok"}

    @app.get("/health/ready", dependencies=[Depends(authorize)])
    def ready():
        urls = {
            "llm": os.getenv("HESTIA_LLM_HEALTH_URL", "http://llm:8080/health"),
            "stt": os.getenv("HESTIA_STT_HEALTH_URL", "http://stt:8001/health"),
            "tts": os.getenv("HESTIA_TTS_HEALTH_URL", "http://tts:8000/health"),
        }
        status: dict[str, bool] = {}
        for name, url in urls.items():
            try:
                response = httpx.get(url, timeout=2.0)
                status[name] = response.status_code == 200
            except httpx.RequestError:
                status[name] = False
        all_ready = all(status.values())
        return Response(
            content=json.dumps({"ready": all_ready, "services": status}),
            media_type="application/json", status_code=200 if all_ready else 503,
        )

    @app.get("/v1/tools", dependencies=[Depends(authorize)])
    def tools():
        return [{"id": tool.id, "name": tool.name, "description": tool.description,
                 "examples": len(tool.examples)} for tool in get_engine().tools.values()]

    @app.post("/v1/tools/reload", dependencies=[Depends(authorize)])
    def reload_tools():
        if engine is not None:
            raise HTTPException(status_code=409, detail="Injected engine cannot be reloaded")
        try:
            previous = get_engine()
            new_tools = load_tools(os.getenv("HESTIA_TOOLS_DIR", "/app/tools"))
            # Reuse the loaded embedding model and LLM clients: do not briefly
            # double model memory when reloading on a small Raspberry Pi.
            new_router = SemanticRouter(
                new_tools, previous.router.embedder,
                min_score=previous.router.min_score,
                min_margin=previous.router.min_margin,
            )
            new_engine = WorkflowEngine(
                new_tools, new_router, previous.llm,
                allowed_permissions=previous.allowed_permissions,
                fallback_llm=previous.fallback_llm,
                max_depth=previous.max_depth,
            )
        except (OSError, RuntimeError, ValueError) as exc:
            raise HTTPException(status_code=422, detail=f"Cannot reload tools: {exc}") from exc
        app.state.engine = new_engine
        return {"tools": len(new_engine.tools), "status": "reloaded"}

    @app.post("/v1/route", dependencies=[Depends(authorize)])
    def route(payload: TextRequest):
        result = get_engine().router.route(payload.text)
        return {"status": result.status, "tool_id": result.tool_id,
                "candidates": [{"tool_id": tid, "score": score}
                               for tid, score in result.candidates]}

    @app.post("/v1/chat", dependencies=[Depends(authorize)])
    def chat(payload: ChatRequest):
        result = get_engine().handle(payload.text, confirmed=payload.confirmed,
                                     context=payload.context)
        return _serialize(result, debug=payload.debug)

    def read_audio(file: UploadFile) -> bytes:
        contents = file.file.read(MAX_AUDIO_BYTES + 1)
        if not contents or len(contents) > MAX_AUDIO_BYTES:
            raise HTTPException(status_code=413, detail="Audio must be 1 byte to 10 MiB")
        return contents

    @app.post("/v1/audio/transcribe", dependencies=[Depends(authorize)])
    def transcribe(file: UploadFile = File(...)):
        try:
            text = get_audio().transcribe(read_audio(file), filename=file.filename or "speech.wav")
        except RuntimeError as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return {"text": text}

    @app.post("/v1/audio/speak", dependencies=[Depends(authorize)])
    def speak(payload: TextRequest):
        try:
            wav = get_audio().synthesize(payload.text)
        except (RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        return Response(content=wav, media_type="audio/wav")

    @app.post("/v1/audio/ask", dependencies=[Depends(authorize)])
    def ask(file: UploadFile = File(...)):
        try:
            transcript = get_audio().transcribe(read_audio(file), filename=file.filename or "speech.wav")
            if not transcript:
                raise HTTPException(status_code=422, detail="Empty transcription")
            result = get_engine().handle(transcript)
            wav = get_audio().synthesize(result.text)
        except (RuntimeError, ValueError, subprocess.SubprocessError) as exc:
            raise HTTPException(status_code=503, detail=str(exc)) from exc
        # Base64 is for the first prototype. Streaming output is a later step.
        import base64
        return {"transcript": transcript, **_serialize(result),
                "audio_base64": base64.b64encode(wav).decode("ascii")}

    return app


app = create_app()
