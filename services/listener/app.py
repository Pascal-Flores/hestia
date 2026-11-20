"""Always-on local microphone satellite, enabled with the Compose `listener` profile.

Capture 48 kHz -> RNNoise -> 16 kHz -> openWakeWord -> endpoint VAD -> STT/Hestia/TTS.
Audio never leaves the local Docker network unless Hestia has a remote fallback.
"""

from __future__ import annotations

import argparse
from collections import deque
import logging
import os
import time

import numpy as np

from .client import HestiaClient, decode_wav
from .dsp import AudioPreprocessor, BLOCK_SAMPLES, INPUT_RATE
from .session import CaptureSettings, UtteranceCapture, wav_bytes
from .wake import WakeDetector

LOG = logging.getLogger("hestia.listener")


def _device(value: str):
    if not value:
        return None
    return int(value) if value.isdecimal() else value


def list_devices():
    import sounddevice as sd
    print(sd.query_devices())


def run(record_sample: float = 0):
    import sounddevice as sd

    input_device = _device(os.getenv("LISTENER_INPUT_DEVICE", ""))
    output_device = _device(os.getenv("LISTENER_OUTPUT_DEVICE", ""))
    mode = os.getenv("LISTENER_FILTER", "rnnoise")
    capture = CaptureSettings(
        min_voice_seconds=float(os.getenv("LISTENER_MIN_VOICE", "0.20")),
        silence_seconds=float(os.getenv("LISTENER_SILENCE", "0.85")),
        initial_timeout_seconds=float(os.getenv("LISTENER_INITIAL_TIMEOUT", "3.5")),
        max_seconds=float(os.getenv("LISTENER_MAX_UTTERANCE", "12.0")),
        rms_threshold=float(os.getenv("LISTENER_RMS_THRESHOLD", "0.008")),
        rnnoise_speech_threshold=float(os.getenv("LISTENER_SPEECH_THRESHOLD", "0.30")),
    )
    preprocessor = AudioPreprocessor(mode)
    LOG.info("Audio processing: %s at %s Hz before wake word AND STT", mode, INPUT_RATE)
    if not record_sample:
        wake_model = os.getenv("LISTENER_WAKE_MODEL", "hey jarvis")
        wake = WakeDetector(wake_model,
                            threshold=float(os.getenv("LISTENER_WAKE_THRESHOLD", "0.55")),
                            consecutive=int(os.getenv("LISTENER_WAKE_CONSECUTIVE", "1")))
        client = HestiaClient(os.getenv("HESTIA_LISTENER_URL", "http://hestia:8765/v1/audio/ask"),
                              api_key=os.getenv("HESTIA_API_KEY", ""))
        LOG.info("Wake model: %s (example default is NOT 'Hestia')", wake_model)
    else:
        wake = None
        client = None

    pre_roll = deque(maxlen=2)  # 160 ms at 16 kHz
    recorder = None
    with sd.RawInputStream(samplerate=INPUT_RATE, channels=1, dtype="int16",
                           blocksize=BLOCK_SAMPLES, device=input_device) as stream:
        if record_sample:
            raw_frames, clean_frames = [], []
            frames_to_capture = max(1, round(record_sample * INPUT_RATE / BLOCK_SAMPLES))
            for _ in range(frames_to_capture):
                raw, overflow = stream.read(BLOCK_SAMPLES)
                if overflow:
                    LOG.warning("Microphone overrun; check CPU load")
                samples = np.frombuffer(raw, dtype=np.int16).copy()
                raw_frames.append(samples)
                clean_frames.append(preprocessor.process(samples).pcm16)
            from pathlib import Path
            output_dir = Path(os.getenv("LISTENER_RECORD_DIR", "/recordings"))
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "raw.wav").write_bytes(wav_bytes(np.concatenate(raw_frames), INPUT_RATE))
            (output_dir / "enhanced.wav").write_bytes(wav_bytes(np.concatenate(clean_frames)))
            LOG.info("Wrote raw.wav and enhanced.wav to %s", output_dir)
            return

        LOG.info("Listening for wake word. Stop with Ctrl+C.")
        try:
            while True:
                raw, overflow = stream.read(BLOCK_SAMPLES)
                if overflow:
                    LOG.warning("Microphone overrun; check CPU load")
                processed = preprocessor.process(np.frombuffer(raw, dtype=np.int16))
                pcm16, speech_score = processed.pcm16, processed.speech_probability
                if recorder is None:
                    pre_roll.append(pcm16.copy())
                    if not wake.predict(pcm16):
                        continue
                    recorder = UtteranceCapture(capture, preroll=list(pre_roll))
                    pre_roll.clear()
                    LOG.info("Wake word detected; listening for command")
                    continue

                status, audio = recorder.feed(pcm16, speech_score)
                if status == "recording":
                    continue
                recorder = None
                wake.reset()
                pre_roll.clear()
                if status == "timeout":
                    LOG.info("No intelligible command; returning to wake word")
                    continue
                # Mute the microphone while generating / playing output to avoid
                # hearing our own TTS. No barge-in in this first version.
                stream.stop()
                try:
                    result = client.ask(audio)
                    LOG.info("Transcription: %s", result.transcript)
                    LOG.info("Hestia [%s]: %s", result.status, result.text)
                    samples, rate = decode_wav(result.wav)
                    sd.play(samples, samplerate=rate, device=output_device, blocking=True)
                except Exception:
                    LOG.exception("Audio round trip failed")
                finally:
                    sd.stop()
                    time.sleep(0.30)  # output device tail / wake word cooldown
                    preprocessor.close()
                    preprocessor = AudioPreprocessor(mode)  # reset RNNoise + FIR context
                    wake.reset()
                    stream.start()
                LOG.info("Listening for wake word")
        finally:
            preprocessor.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hestia always-on microphone listener")
    parser.add_argument("--list-devices", action="store_true", help="List PortAudio devices without loading models")
    parser.add_argument("--record-sample", type=float, default=0.0,
                        help="Save raw and enhanced WAV for N seconds, without wake word")
    args = parser.parse_args(argv)
    logging.basicConfig(level=os.getenv("LISTENER_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.list_devices:
        list_devices()
    else:
        run(record_sample=args.record_sample)


if __name__ == "__main__":
    main()
