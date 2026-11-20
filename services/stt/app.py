"""Small persistent faster-whisper service for Hestia."""

from __future__ import annotations

from contextlib import asynccontextmanager
import os
from pathlib import Path
import tempfile

from fastapi import FastAPI, File, HTTPException, UploadFile

MAX_AUDIO_BYTES = 10 * 1024 * 1024


@asynccontextmanager
async def lifespan(app: FastAPI):
    from faster_whisper import WhisperModel
    app.state.model = WhisperModel(
        os.getenv("STT_MODEL", "tiny"),
        device="cpu", compute_type="int8",
        cpu_threads=int(os.getenv("STT_CPU_THREADS", "2")),
    )
    yield


app = FastAPI(title="Hestia STT", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/v1/transcribe")
def transcribe(file: UploadFile = File(...)):
    audio = file.file.read(MAX_AUDIO_BYTES + 1)
    if not audio or len(audio) > MAX_AUDIO_BYTES:
        raise HTTPException(status_code=413, detail="Audio must be 1 byte to 10 MiB")
    suffix = Path(file.filename or "speech.wav").suffix.lower()
    if suffix not in {".wav", ".mp3", ".m4a", ".flac", ".ogg", ".webm"}:
        raise HTTPException(status_code=415, detail="Unsupported audio extension")
    with tempfile.NamedTemporaryFile(suffix=suffix) as temp:
        temp.write(audio)
        temp.flush()
        try:
            segments, _ = app.state.model.transcribe(
                temp.name, language=os.getenv("STT_LANGUAGE", "fr"), vad_filter=True,
            )
            text = " ".join(segment.text.strip() for segment in segments).strip()
        except Exception as exc:
            raise HTTPException(status_code=422, detail="Could not transcribe audio") from exc
    return {"text": text}
