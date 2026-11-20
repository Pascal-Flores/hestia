"""Always-on microphone listener with nonblocking capture and optional RNNoise.

Capture 48 kHz -> RNNoise -> 16 kHz -> openWakeWord -> endpoint VAD -> STT/Hestia/TTS.
The PortAudio callback *only* queues raw PCM. Denoising and inference run on
an independent processing loop so transient CPU spikes do not block capture.
"""

from __future__ import annotations

import argparse
from collections import deque
import logging
import os
import time

import numpy as np

from .capture import BufferedMicrophone
from .client import HestiaClient
from .playback import play_wav
from .diagnostics import WakeDiagnostics
from .dsp import AudioPreprocessor, BLOCK_SAMPLES, INPUT_RATE
from .session import CaptureSettings, UtteranceCapture, wav_bytes
from .wake import WakeDetector

LOG = logging.getLogger("hestia.listener")
BLOCK_SECONDS = BLOCK_SAMPLES / INPUT_RATE


def _device(value: str):
    if not value:
        return None
    return int(value) if value.isdecimal() else value


def list_devices():
    import sounddevice as sd
    print(sd.query_devices())


class ProcessingMetrics:
    """Measure actual per-block CPU pressure and queue discontinuities."""

    def __init__(self, report_seconds: float = 30.0):
        if report_seconds <= 0:
            raise ValueError("LISTENER_METRICS_INTERVAL must be positive")
        self.report_seconds = report_seconds
        self.reset()

    def reset(self):
        self.started_at = time.monotonic()
        self.blocks = 0
        self.total_ms = 0.0
        self.max_ms = 0.0
        self.max_queued = 0
        self.missed = 0
        self.overflows = 0
        self.filter_ms = 0.0
        self.wake_ms = 0.0
        self.wake_calls = 0

    def observe(self, *, processing_ms: float, queued_blocks: int,
                missed_blocks: int, input_overflow: bool,
                filter_ms: float = 0.0, wake_ms: float = 0.0,
                wake_invoked: bool = False) -> None:
        self.blocks += 1
        self.total_ms += processing_ms
        self.max_ms = max(self.max_ms, processing_ms)
        self.max_queued = max(self.max_queued, queued_blocks)
        self.missed += missed_blocks
        self.overflows += int(input_overflow)
        self.filter_ms += filter_ms
        self.wake_ms += wake_ms
        self.wake_calls += int(wake_invoked)

    def report_if_due(self):
        if time.monotonic() - self.started_at < self.report_seconds:
            return
        if self.blocks:
            average = self.total_ms / self.blocks
            warn = self.missed or self.overflows or average > BLOCK_SECONDS * 1000 * 0.85
            log = LOG.warning if warn else LOG.info
            log("Microphone stats: avg processing=%.1f ms (budget=%.0f ms), "
                "filter=%.1f ms, wake=%.1f ms (%d calls), max=%.1f ms, "
                "max queued=%d blocks, dropped=%d blocks, PortAudio overflows=%d",
                average, BLOCK_SECONDS * 1000, self.filter_ms / self.blocks,
                self.wake_ms / self.wake_calls if self.wake_calls else 0.0,
                self.wake_calls, self.max_ms, self.max_queued, self.missed,
                self.overflows)
        self.reset()


def run(record_sample: float = 0, test_once: float = 0):
    import sounddevice as sd

    input_device = _device(os.getenv("LISTENER_INPUT_DEVICE", ""))
    output_device = _device(os.getenv("LISTENER_OUTPUT_DEVICE", ""))
    output_rate = int(os.getenv("LISTENER_OUTPUT_RATE", "0")) or None
    debug_audio = os.getenv("LISTENER_DEBUG_AUDIO", "0").strip().lower() in ("1", "true", "yes", "on")
    mode = os.getenv("LISTENER_FILTER", "rnnoise")
    queue_blocks = int(os.getenv("LISTENER_QUEUE_BLOCKS", "16"))
    input_latency = os.getenv("LISTENER_INPUT_LATENCY", "").strip() or None
    pa_blocksize = int(os.getenv("LISTENER_PORTAUDIO_BLOCKSIZE", "0"))
    metrics = ProcessingMetrics(float(os.getenv("LISTENER_METRICS_INTERVAL", "30")))
    debug_wake = os.getenv("LISTENER_DEBUG_WAKE", "").strip().lower() in ("1", "true", "yes", "on")
    debug_interval = float(os.getenv("LISTENER_DIAGNOSTICS_INTERVAL", "5"))
    diagnostics = None
    capture = CaptureSettings(
        min_voice_seconds=float(os.getenv("LISTENER_MIN_VOICE", "0.20")),
        silence_seconds=float(os.getenv("LISTENER_SILENCE", "0.85")),
        initial_timeout_seconds=float(os.getenv("LISTENER_INITIAL_TIMEOUT", "3.5")),
        max_seconds=float(os.getenv("LISTENER_MAX_UTTERANCE", "12.0")),
        rms_threshold=float(os.getenv("LISTENER_RMS_THRESHOLD", "0.008")),
        rnnoise_speech_threshold=float(os.getenv("LISTENER_SPEECH_THRESHOLD", "0.30")),
    )
    preprocessor = AudioPreprocessor(mode)
    LOG.info("Audio processing: %s at %s Hz; input queue=%d blocks (%.2f sec); "
             "PortAudio blocksize=%d (0=driver-chosen), latency=%s",
             mode, INPUT_RATE, queue_blocks, BLOCK_SECONDS * queue_blocks,
             pa_blocksize, input_latency or "default")
    if not record_sample:
        client = HestiaClient(os.getenv("HESTIA_LISTENER_URL", "http://hestia:8765/v1/audio/ask"),
                              api_key=os.getenv("HESTIA_API_KEY", ""))
    else:
        client = None
    if not record_sample and not test_once:
        wake_model = os.getenv("LISTENER_WAKE_MODEL", "hey jarvis")
        wake = WakeDetector(wake_model,
                            threshold=float(os.getenv("LISTENER_WAKE_THRESHOLD", "0.55")),
                            consecutive=int(os.getenv("LISTENER_WAKE_CONSECUTIVE", "1")))
        LOG.info("Wake model: %s (example default is NOT 'Hestia')", wake_model)
        if debug_wake:
            diagnostics = WakeDiagnostics(
                threshold=wake.threshold, report_seconds=debug_interval, logger=LOG
            )
            LOG.info("Wake diagnostics enabled; report interval=%.1f seconds", debug_interval)
    else:
        wake = None

    pre_roll = deque(maxlen=2)  # 160 ms at 16 kHz
    recorder = None
    with BufferedMicrophone(device=input_device, max_blocks=queue_blocks,
                            latency=input_latency,
                            portaudio_blocksize=pa_blocksize) as microphone:
        if record_sample or test_once:
            raw_frames, clean_frames = [], []
            seconds = record_sample or test_once
            frames_to_capture = max(1, round(seconds / BLOCK_SECONDS))
            LOG.info("Recording %.1fs of microphone audio without wake word", seconds)
            for _ in range(frames_to_capture):
                block = microphone.read()
                if block.missed_blocks or block.input_overflow:
                    LOG.warning("Audio discontinuity while recording sample: dropped=%d, "
                                "PortAudio overflow=%s", block.missed_blocks, block.input_overflow)
                raw_frames.append(block.pcm.copy())
                clean_frames.append(preprocessor.process(block.pcm).pcm16)
            from pathlib import Path
            output_dir = Path(os.getenv("LISTENER_RECORD_DIR", "/recordings"))
            output_dir.mkdir(parents=True, exist_ok=True)
            (output_dir / "raw.wav").write_bytes(wav_bytes(np.concatenate(raw_frames), INPUT_RATE))
            (output_dir / "enhanced.wav").write_bytes(wav_bytes(np.concatenate(clean_frames)))
            LOG.info("Wrote raw.wav and enhanced.wav to %s", output_dir)
            preprocessor.close()
            if test_once:
                # A single recorded request bypasses wake word and VAD to test
                # microphone -> STT -> route/LLM -> Pocket TTS -> speaker.
                microphone.stop()
                LOG.info("Sending enhanced recording to Hestia (wake word bypassed)")
                result = client.ask(wav_bytes(np.concatenate(clean_frames)))
                LOG.info("STT transcript: %s", result.transcript)
                LOG.info("Hestia [%s]: %s", result.status, result.text)
                (output_dir / "once-response.wav").write_bytes(result.wav)
                LOG.info("Saved response to %s", output_dir / "once-response.wav")
                try:
                    play_wav(result.wav, device=output_device, requested_rate=output_rate,
                             sd=sd, logger=LOG)
                finally:
                    sd.stop()
            return

        LOG.info("Listening for wake word. Stop with Ctrl+C.")
        try:
            while True:
                block = microphone.read()
                started = time.perf_counter()
                # Missing blocks invalidate streaming inference and utterances;
                # restart wake/VAD rather than treating discontinuous audio as one phrase.
                if block.missed_blocks or block.input_overflow:
                    recorder = None
                    pre_roll.clear()
                    wake.reset()
                    preprocessor.close()
                    preprocessor = AudioPreprocessor(mode)

                before_filter = time.perf_counter()
                processed = preprocessor.process(block.pcm)
                filter_ms = (time.perf_counter() - before_filter) * 1000
                pcm16, speech_score = processed.pcm16, processed.speech_probability
                state, audio = "listening", None
                wake_ms, wake_invoked = 0.0, False
                if recorder is None:
                    pre_roll.append(pcm16.copy())
                    wake_start = time.perf_counter()
                    detected = wake.predict(pcm16)
                    wake_ms = (time.perf_counter() - wake_start) * 1000
                    wake_invoked = True
                    if detected:
                        recorder = UtteranceCapture(capture, preroll=list(pre_roll))
                        pre_roll.clear()
                        state = "activated"
                else:
                    state, audio = recorder.feed(pcm16, speech_score)
                    if state != "recording":
                        recorder = None
                        wake.reset()
                        pre_roll.clear()

                if diagnostics is not None:
                    diagnostics.observe(
                        raw=block.pcm, clean=pcm16,
                        wake_score=wake.last_score if wake_invoked else None,
                    )
                    diagnostics.report_if_due()

                metrics.observe(
                    processing_ms=(time.perf_counter() - started) * 1000,
                    queued_blocks=block.queued_blocks,
                    missed_blocks=block.missed_blocks,
                    input_overflow=block.input_overflow,
                    filter_ms=filter_ms,
                    wake_ms=wake_ms,
                    wake_invoked=wake_invoked,
                )
                metrics.report_if_due()

                if state == "activated":
                    LOG.info("Wake word detected (score=%.3f); listening for command",
                             wake.last_score)
                elif state == "timeout":
                    LOG.info("No intelligible command; returning to wake word")
                elif state == "complete":
                    # Pause capture while speaking: otherwise our TTS triggers the mic.
                    microphone.stop()
                    try:
                        if debug_audio:
                            from pathlib import Path
                            output_dir = Path(os.getenv("LISTENER_RECORD_DIR", "/recordings"))
                            output_dir.mkdir(parents=True, exist_ok=True)
                            (output_dir / "last-command.wav").write_bytes(audio)
                            LOG.info("Saved actual STT input to %s",
                                     output_dir / "last-command.wav")
                        result = client.ask(audio)
                        LOG.info("Transcription: %s", result.transcript)
                        LOG.info("Hestia [%s]: %s", result.status, result.text)
                        if debug_audio:
                            (output_dir / "last-response.wav").write_bytes(result.wav)
                            LOG.info("Saved TTS WAV to %s", output_dir / "last-response.wav")
                        play_wav(result.wav, device=output_device, requested_rate=output_rate,
                                 sd=sd, logger=LOG)
                    except Exception:
                        LOG.exception("Audio round trip failed")
                    finally:
                        sd.stop()
                        time.sleep(0.30)
                        preprocessor.close()
                        preprocessor = AudioPreprocessor(mode)
                        wake.reset()
                        metrics.reset()
                        microphone.start()
                    LOG.info("Listening for wake word")
        finally:
            preprocessor.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="Hestia always-on microphone listener")
    parser.add_argument("--list-devices", action="store_true", help="List PortAudio devices without loading models")
    parser.add_argument("--record-sample", type=float, default=0.0,
                        help="Save raw and enhanced WAV for N seconds, without wake word")
    parser.add_argument("--test-once", type=float, default=0.0, metavar="SECONDS",
                        help="Record N seconds, call Hestia and play/save the audio reply (no wake word)")
    args = parser.parse_args(argv)
    if args.record_sample and args.test_once:
        parser.error("--record-sample and --test-once cannot be combined")
    if args.record_sample < 0 or args.test_once < 0:
        parser.error("Audio recording duration must be positive")
    logging.basicConfig(level=os.getenv("LISTENER_LOG_LEVEL", "INFO"),
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    if args.list_devices:
        list_devices()
    else:
        run(record_sample=args.record_sample, test_once=args.test_once)


if __name__ == "__main__":
    main()
