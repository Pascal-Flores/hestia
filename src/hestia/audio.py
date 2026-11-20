"""Optional file-based STT and Pocket TTS adapters. No microphone daemon in V1."""

from __future__ import annotations

from pathlib import Path
import subprocess
import tempfile

from .speech import normalize_for_speech


def transcribe(wav_file: str | Path, *, model_name: str = "tiny", language: str = "fr") -> str:
    """Transcribe a recorded file locally with faster-whisper (optional dependency)."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError("Install STT support: pip install '.[voice]'") from exc
    model = WhisperModel(model_name, device="cpu", compute_type="int8")
    segments, _ = model.transcribe(str(wav_file), language=language, vad_filter=True)
    return " ".join(segment.text.strip() for segment in segments).strip()


def synthesize(text: str, output: str | Path, *, language: str = "french",
               voice: str | None = None, profile: str = "natural") -> Path:
    """Generate a WAV with pocket-tts; optional SoX filter for stylized voices."""
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory() as temporary:
        raw = Path(temporary) / "raw.wav"
        spoken = normalize_for_speech(text) if language.lower() in ("fr", "fr_fr", "french") else text
        command = ["pocket-tts", "generate", "--language", language,
                   "--text", spoken, "--output-path", str(raw)]
        if voice:
            command.extend(["--voice", voice])
        subprocess.run(command, check=True, timeout=180)
        if profile == "mii":
            subprocess.run(["sox", str(raw), str(destination), "pitch", "250",
                            "highpass", "180", "lowpass", "5000"], check=True, timeout=45)
        elif profile == "natural":
            destination.write_bytes(raw.read_bytes())
        else:
            raise ValueError("Unknown voice profile: " + profile)
    return destination
