"""Realtime PortAudio capture, isolated from denoising and wake inference.

PortAudio's callback copies PCM into a small bounded queue and never runs ML.
The processing loop drains this queue at its own pace. If it cannot keep up,
older chunks are dropped deliberately and discontinuities are reported so
wake/VAD state can be reset instead of treating interrupted audio as speech.
"""

from __future__ import annotations

from dataclasses import dataclass
from queue import Empty, Full, Queue

import numpy as np

from .dsp import BLOCK_SAMPLES, INPUT_RATE


@dataclass(frozen=True)
class AudioBlock:
    pcm: np.ndarray
    missed_blocks: int
    input_overflow: bool
    queued_blocks: int


class BufferedMicrophone:
    """PortAudio auto-sized callbacks, repacked into 80 ms frames for the worker."""

    def __init__(self, *, device=None, max_blocks: int = 16, latency: str | None = None,
                 portaudio_blocksize: int = 0):
        if not isinstance(max_blocks, int) or not 2 <= max_blocks <= 128:
            raise ValueError("LISTENER_QUEUE_BLOCKS must be between 2 and 128")
        if latency not in (None, "low", "high"):
            raise ValueError("LISTENER_INPUT_LATENCY must be 'low' or 'high'")
        if (isinstance(portaudio_blocksize, bool) or not isinstance(portaudio_blocksize, int)
                or not 0 <= portaudio_blocksize <= 19200):
            raise ValueError("LISTENER_PORTAUDIO_BLOCKSIZE must be 0-19200")
        self.device = device
        self.max_blocks = max_blocks
        self.latency = latency
        self.portaudio_blocksize = portaudio_blocksize
        # PortAudio may deliver different frame counts with blocksize=0.
        # Assemble exact 80 ms application frames before enqueuing them.
        self._partial = bytearray()
        self._partial_overflow = False
        self._queue: Queue[tuple[int, bytes, bool]] = Queue(maxsize=max_blocks)
        self._next_sequence = 0
        self._expected_sequence = 0
        self._stream = None

    def _callback(self, indata, frames, _time_info, status) -> None:
        """PortAudio realtime callback: only copy audio and enqueue it.

        Do not log, denoise, infer, allocate arrays or make network calls here.
        """
        # A hardware overflow may split an application frame; never splice
        # audio from opposite sides of that discontinuity.
        if getattr(status, "input_overflow", False):
            self._partial.clear()
            self._partial_overflow = True
        if len(indata) != frames * 2:
            # Invalid PCM: discard the partial frame and mark a discontinuity.
            # Do not raise inside a PortAudio callback.
            self._partial.clear()
            self._partial_overflow = True
            return
        self._partial.extend(indata)
        bytes_per_block = BLOCK_SAMPLES * 2
        while len(self._partial) >= bytes_per_block:
            raw = bytes(self._partial[:bytes_per_block])
            del self._partial[:bytes_per_block]
            seq = self._next_sequence
            self._next_sequence += 1
            entry = (seq, raw, self._partial_overflow)
            self._partial_overflow = False
            try:
                self._queue.put_nowait(entry)
            except Full:
                # Drop the oldest completed block rather than building latency.
                try:
                    self._queue.get_nowait()
                except Empty:
                    pass
                self._queue.put_nowait(entry)

    def read(self, timeout: float = 5.0) -> AudioBlock:
        """Wait for one block, returning exact discontinuity and buffer metrics."""
        try:
            sequence, raw, overflow = self._queue.get(timeout=timeout)
        except Empty as exc:
            raise RuntimeError("Microphone stopped delivering audio blocks") from exc
        missed = max(0, sequence - self._expected_sequence)
        self._expected_sequence = sequence + 1
        if len(raw) != BLOCK_SAMPLES * 2:
            raise RuntimeError(f"Unexpected PCM size: {len(raw)} bytes")
        pcm = np.frombuffer(raw, dtype=np.int16)
        return AudioBlock(pcm, missed, overflow, self._queue.qsize())

    def _discard_queued(self) -> None:
        while True:
            try:
                self._queue.get_nowait()
            except Empty:
                break
        self._expected_sequence = self._next_sequence
        self._partial.clear()
        self._partial_overflow = False

    def stop(self) -> None:
        if self._stream is not None:
            self._stream.stop()
        self._discard_queued()

    def start(self) -> None:
        if self._stream is None:
            raise RuntimeError("Microphone stream is not open")
        self._discard_queued()
        self._stream.start()

    def __enter__(self) -> "BufferedMicrophone":
        import sounddevice as sd

        options = dict(samplerate=INPUT_RATE, channels=1, dtype="int16",
                       blocksize=self.portaudio_blocksize, device=self.device,
                       callback=self._callback)
        if self.latency is not None:
            options["latency"] = self.latency
        self._stream = sd.RawInputStream(**options)
        try:
            self.start()
        except BaseException:
            self._stream.close()
            self._stream = None
            raise
        return self

    def __exit__(self, _exc_type, _exc, _tb) -> None:
        if self._stream is not None:
            try:
                self.stop()
            finally:
                self._stream.close()
                self._stream = None
