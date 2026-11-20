"""Realtime capture tests, using synthetic callback invocations (no sound card)."""

import logging
from pathlib import Path
import sys

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.listener.app import ProcessingMetrics
from services.listener.capture import BufferedMicrophone
from services.listener.dsp import BLOCK_SAMPLES


class FakeStatus:
    def __init__(self, overflow=False):
        self.input_overflow = overflow


def push(mic: BufferedMicrophone, sample: int, *, overflow: bool = False):
    chunk = np.full(BLOCK_SAMPLES, sample, np.int16)
    mic._callback(chunk.tobytes(), BLOCK_SAMPLES, None, FakeStatus(overflow))


def test_callback_keeps_latest_audio_with_bounded_queue():
    mic = BufferedMicrophone(max_blocks=2)
    for n in range(4):
        push(mic, n, overflow=n == 3)
    first = mic.read(timeout=0.01)
    second = mic.read(timeout=0.01)
    assert first.missed_blocks == 2
    assert first.queued_blocks == 1
    assert first.input_overflow is False
    assert first.pcm.size == BLOCK_SAMPLES
    assert np.all(first.pcm == 2)
    assert second.missed_blocks == 0
    assert second.input_overflow is True
    assert np.all(second.pcm == 3)
    with pytest.raises(RuntimeError, match="stopped delivering"):
        mic.read(timeout=0.001)


def test_sequential_blocks_need_no_recovery():
    mic = BufferedMicrophone(max_blocks=4)
    push(mic, 40)
    push(mic, 80)
    assert mic.read(timeout=0.01).missed_blocks == 0
    assert mic.read(timeout=0.01).missed_blocks == 0


def test_pause_drops_old_audio_before_restarting():
    class FakeStream:
        def __init__(self):
            self.running = True
            self.stop_calls = 0
            self.start_calls = 0

        def stop(self):
            self.stop_calls += 1
            self.running = False

        def start(self):
            self.start_calls += 1
            self.running = True

    mic = BufferedMicrophone(max_blocks=2)
    mic._stream = FakeStream()
    push(mic, 4)
    mic.stop()
    assert mic._stream.stop_calls == 1
    mic.start()
    push(mic, 5)
    fresh = mic.read(timeout=0.01)
    assert fresh.missed_blocks == 0
    assert np.all(fresh.pcm == 5)
    assert mic._stream.start_calls == 1


def test_validation_and_bad_pcm_size():
    with pytest.raises(ValueError):
        BufferedMicrophone(max_blocks=1)
    with pytest.raises(ValueError):
        BufferedMicrophone(latency="instant")
    mic = BufferedMicrophone()
    mic._callback(b"123", BLOCK_SAMPLES, None, FakeStatus())
    push(mic, 99)
    assert mic.read(timeout=0.01).input_overflow is True
    with pytest.raises(ValueError, match="PORTAUDIO_BLOCKSIZE"):
        BufferedMicrophone(portaudio_blocksize=-1)


def test_metrics_reports_performance_budget_and_drops(monkeypatch, caplog):
    now = [0.0]
    monkeypatch.setattr("services.listener.app.time.monotonic", lambda: now[0])
    metrics = ProcessingMetrics(report_seconds=1)
    metrics.observe(processing_ms=76, queued_blocks=11, missed_blocks=2, input_overflow=True)
    now[0] = 2.0
    with caplog.at_level(logging.WARNING, logger="hestia.listener"):
        metrics.report_if_due()
    assert "avg processing=76.0 ms" in caplog.text
    assert "dropped=2 blocks" in caplog.text
    assert "PortAudio overflows=1" in caplog.text
    assert metrics.blocks == 0



def test_auto_blocksize_reassembles_variable_portaudio_frames():
    mic = BufferedMicrophone(max_blocks=4)
    assert mic.portaudio_blocksize == 0
    first = np.full(960, 10, np.int16).tobytes()
    second = np.full(1920, 20, np.int16).tobytes()
    third = np.full(960, 30, np.int16).tobytes()
    mic._callback(first, 960, None, FakeStatus())
    mic._callback(second, 1920, None, FakeStatus())
    assert mic._queue.empty()
    mic._callback(third, 960, None, FakeStatus())
    block = mic.read(timeout=0.01)
    np.testing.assert_array_equal(
        block.pcm, np.concatenate([
            np.full(960, 10, np.int16),
            np.full(1920, 20, np.int16),
            np.full(960, 30, np.int16),
        ])
    )
    assert block.input_overflow is False
    assert block.missed_blocks == 0


def test_variable_callbacks_can_produce_multiple_application_frames():
    mic = BufferedMicrophone(max_blocks=4)
    pcm = np.arange(BLOCK_SAMPLES * 2 + 480, dtype=np.int16)
    mic._callback(pcm.tobytes(), len(pcm), None, FakeStatus())
    np.testing.assert_array_equal(mic.read(timeout=0.01).pcm, pcm[:BLOCK_SAMPLES])
    np.testing.assert_array_equal(mic.read(timeout=0.01).pcm, pcm[BLOCK_SAMPLES:BLOCK_SAMPLES * 2])
    assert mic._queue.empty()
    mic._callback(np.zeros(BLOCK_SAMPLES - 480, np.int16).tobytes(),
                  BLOCK_SAMPLES - 480, None, FakeStatus())
    assert mic.read(timeout=0.01).pcm.size == BLOCK_SAMPLES


def test_input_overflow_does_not_join_audio_across_gap():
    mic = BufferedMicrophone(max_blocks=4)
    before_gap = np.full(1920, 1, np.int16)
    after_gap = np.full(1920, 2, np.int16)
    remainder = np.full(1920, 3, np.int16)
    mic._callback(before_gap.tobytes(), 1920, None, FakeStatus())
    mic._callback(after_gap.tobytes(), 1920, None, FakeStatus(True))
    mic._callback(remainder.tobytes(), 1920, None, FakeStatus())
    block = mic.read(timeout=0.01)
    assert block.input_overflow
    np.testing.assert_array_equal(
        block.pcm, np.concatenate([after_gap, remainder])
    )


def test_pa_fixed_size_can_be_restored_and_pending_audio_is_cleared():
    mic = BufferedMicrophone(max_blocks=3, portaudio_blocksize=BLOCK_SAMPLES)
    half = np.full(1920, 3, np.int16).tobytes()
    mic._callback(half, 1920, None, FakeStatus())
    mic._discard_queued()
    mic._callback(half, 1920, None, FakeStatus())
    assert mic._queue.empty()
    mic._callback(half, 1920, None, FakeStatus())
    assert mic.read(timeout=0.01).pcm.size == BLOCK_SAMPLES
