"""Device settings and private HTTP API tests without physical microphone access."""

from __future__ import annotations

import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from services.listener.control import DeviceControl, AudioControlServer


class FakePortAudio:
    class PortAudioError(Exception):
        pass

    def __init__(self):
        self.default = type("Defaults", (), {"device": (2, 1)})()
        self.devices = [
            {"name": "HDMI Output", "max_input_channels": 0, "max_output_channels": 8,
             "default_samplerate": 48000.0},
            {"name": "CX11970 Analog", "max_input_channels": 2, "max_output_channels": 2,
             "default_samplerate": 48000.0},
            {"name": "ACP microphone", "max_input_channels": 2, "max_output_channels": 0,
             "default_samplerate": 48000.0},
            {"name": "Invalid input", "max_input_channels": 1, "max_output_channels": 0,
             "default_samplerate": 48000.0},
            {"name": "Unsupported output", "max_input_channels": 0, "max_output_channels": 1,
             "default_samplerate": 48000.0},
        ]
        self.input_calls = []
        self.output_calls = []

    def query_devices(self, index=None):
        if index is None:
            return self.devices
        if index < 0 or index >= len(self.devices):
            raise ValueError("No such device")
        return self.devices[index]

    def check_input_settings(self, *, device, samplerate, channels, dtype):
        self.input_calls.append((device, samplerate, channels, dtype))
        if device == 3:
            raise self.PortAudioError("Invalid sample rate")

    def check_output_settings(self, *, device, samplerate, channels, dtype):
        self.output_calls.append((device, samplerate, channels, dtype))
        if device == 4:
            raise self.PortAudioError("Invalid sample rate")


def test_device_discovery_lists_actual_portaudio_devices(tmp_path):
    control = DeviceControl(tmp_path / "device-settings.json", device_api=FakePortAudio(),
                            environ={"LISTENER_INPUT_DEVICE": "2",
                                     "LISTENER_OUTPUT_DEVICE": "1"})
    data = control.list_devices()
    assert data["input_device"] == 2
    assert data["output_device"] == 1
    assert data["default_input"] == 2
    assert data["default_output"] == 1
    assert data["devices"][0]["max_input_channels"] == 0
    assert data["devices"][2]["name"] == "ACP microphone"
    assert data["devices"][2]["default_input"]
    assert data["devices"][1]["default_output"]


def test_update_validates_persists_restarts_and_restores(tmp_path):
    path = tmp_path / "device-settings.json"
    audio = FakePortAudio()
    control = DeviceControl(path, device_api=audio, environ={})
    assert control.current() == (None, None)
    assert not control.changed.is_set()
    data = control.update({"input_device": 2, "output_device": 1})
    assert data["input_device"] == 2 and data["output_device"] == 1
    assert control.changed.is_set()
    assert audio.input_calls == [(2, 48000, 1, "int16")]
    assert audio.output_calls[0] == (1, 48000, 1, "int16")
    assert json.loads(path.read_text()) == {"input_device": 2, "output_device": 1}
    assert path.stat().st_mode & 0o777 == 0o600
    assert control.current() == (2, 1)
    assert not control.changed.is_set()
    # Saving the same selection again must not deadlock or restart capture.
    control.update({"input_device": 2, "output_device": 1})
    assert not control.changed.is_set()
    restored = DeviceControl(path, device_api=audio,
                             environ={"LISTENER_INPUT_DEVICE": "1"})
    assert restored.current() == (2, 1)
    control.update({"input_device": None, "output_device": None})
    assert control.current() == (None, None)


@pytest.mark.parametrize("selection", [
    {"input_device": 0, "output_device": 1},  # HDMI cannot capture
    {"input_device": 2, "output_device": 2},  # ACP cannot play
    {"input_device": 3, "output_device": 1},  # Unsupported input samplerate
    {"input_device": 2, "output_device": 4},  # Unsupported output samplerates
    {"input_device": 17, "output_device": 1},
    {"input_device": True, "output_device": 1},
    {"input_device": -1, "output_device": 1},
    {"input_device": "2", "output_device": 1},
    {"input_device": 2},
])
def test_invalid_device_choices_do_not_modify_existing_settings(tmp_path, selection):
    path = tmp_path / "device-settings.json"
    control = DeviceControl(path, device_api=FakePortAudio(), environ={})
    with pytest.raises(ValueError):
        control.update(selection)
    assert control.current() == (None, None)
    assert not path.exists()


def test_control_http_endpoints_require_key_and_change_selection(tmp_path):
    control = DeviceControl(tmp_path / "device-settings.json",
                            device_api=FakePortAudio(), environ={})
    server = AudioControlServer(("127.0.0.1", 0), control, "test-secret")
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/devices"

    def call(method, data=None, *, key="test-secret"):
        headers = {"X-Hestia-Key": key}
        if data is not None:
            headers["Content-Type"] = "application/json"
        request = Request(url, headers=headers, method=method,
                          data=json.dumps(data).encode() if data is not None else None)
        with urlopen(request, timeout=3) as response:
            return response.status, json.loads(response.read())

    try:
        with pytest.raises(HTTPError) as unauthorized:
            call("GET", key="")
        assert unauthorized.value.code == 401
        status, choices = call("GET")
        assert status == 200 and len(choices["devices"]) == 5
        status, result = call("PUT", {"input_device": 2, "output_device": 1})
        assert status == 200 and result["input_device"] == 2
        assert control.changed.is_set()
        status, reread = call("GET")
        assert status == 200 and reread["output_device"] == 1
        with pytest.raises(HTTPError) as invalid:
            call("PUT", {"input_device": 2, "output_device": 2})
        assert invalid.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
