"""Internal-only microphone/speaker selection service for Hestia's web settings.

The control server runs *inside the listener container*, where PortAudio sees
/dev/snd. The Rust gateway proxies its authenticated admin requests to this
service; nothing is published on the host. A changed selection signals the
capture loop to release and reopen its stream without a container restart.
"""

from __future__ import annotations

import hmac
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import logging
import os
from pathlib import Path
import threading

LOG = logging.getLogger("hestia.listener.control")


def _env_device(value: str):
    if not value:
        return None
    return int(value) if value.isdecimal() else value


def _default_index(value):
    return int(value) if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class DeviceControl:
    """Thread-safe persisted selections; only explicitly chosen indices are stored."""

    def __init__(self, path: str | Path, *, device_api=None, environ=None):
        if device_api is None:
            import sounddevice as device_api
        self.sd = device_api
        self.path = Path(path)
        env = os.environ if environ is None else environ
        self._selection = {
            "input_device": _env_device(env.get("LISTENER_INPUT_DEVICE", "")),
            "output_device": _env_device(env.get("LISTENER_OUTPUT_DEVICE", "")),
        }
        self._lock = threading.Lock()
        self.changed = threading.Event()
        if self.path.is_file():
            loaded = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(loaded, dict) or set(loaded) != set(self._selection):
                raise ValueError("Invalid saved audio device settings")
            for name in self._selection:
                self._check_type(loaded[name])
            self._selection = loaded
        LOG.info("Audio selection loaded: input=%r output=%r",
                 self._selection["input_device"], self._selection["output_device"])

    @staticmethod
    def _check_type(value):
        if value is not None and (isinstance(value, bool) or not isinstance(value, int) or value < 0):
            raise ValueError("Device index must be a non-negative integer or null (automatic)")

    def current(self):
        """Called when (re)opening the audio stream; marks that version consumed."""
        with self._lock:
            self.changed.clear()
            return self._selection["input_device"], self._selection["output_device"]

    def get(self):
        with self._lock:
            return dict(self._selection)

    def list_devices(self):
        devices = []
        defaults = self.sd.default.device
        inp = _default_index(defaults[0])
        out = _default_index(defaults[1])
        for idx, device in enumerate(self.sd.query_devices()):
            devices.append({
                "id": idx,
                "name": device["name"],
                "max_input_channels": int(device["max_input_channels"]),
                "max_output_channels": int(device["max_output_channels"]),
                "default_samplerate": float(device["default_samplerate"]),
                "default_input": idx == inp,
                "default_output": idx == out,
            })
        return {
            "devices": devices,
            "default_input": inp,
            "default_output": out,
            **self.get(),
        }

    def _validate(self, device_id, *, kind):
        self._check_type(device_id)
        if device_id is None:
            return
        try:
            info = self.sd.query_devices(device_id)
        except (ValueError, IndexError) as error:
            raise ValueError(f"Unknown {kind} device {device_id}") from error
        if int(info[f"max_{kind}_channels"]) < 1:
            raise ValueError(f"Device {device_id} has no {kind} channels")
        if kind == "input":
            try:
                self.sd.check_input_settings(
                    device=device_id, samplerate=48000, channels=1, dtype="int16"
                )
            except Exception as error:
                raise ValueError(
                    f"Microphone {device_id} does not support 48 kHz mono input: {error}"
                ) from error
        else:
            # Pocket TTS output can be resampled to one of these output rates.
            source = int(round(float(info["default_samplerate"])))
            rates = list(dict.fromkeys([source, 48000, 44100, 24000, 16000]))
            for rate in rates:
                if rate <= 0:
                    continue
                try:
                    self.sd.check_output_settings(
                        device=device_id, samplerate=rate, channels=1, dtype="int16"
                    )
                    return
                except Exception:
                    continue
            raise ValueError(f"Speaker {device_id} does not accept mono PCM output")

    def update(self, selection):
        if not isinstance(selection, dict) or set(selection) != {"input_device", "output_device"}:
            raise ValueError("Expected input_device and output_device")
        for name, value in selection.items():
            self._check_type(value)
            self._validate(value, kind="input" if name == "input_device" else "output")
        if selection == self.get():
            return self.list_devices()
        with self._lock:
            # Do not change the active selection if persistence fails.
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_name(self.path.name + ".tmp")
            try:
                with tmp.open("w", encoding="utf-8") as stream:
                    json.dump(selection, stream)
                    stream.write("\n")
                os.chmod(tmp, 0o600)
                os.replace(tmp, self.path)
            finally:
                if tmp.exists():
                    tmp.unlink()
            self._selection = dict(selection)
            self.changed.set()
            LOG.info("Audio selection updated: input=%r output=%r",
                     selection["input_device"], selection["output_device"])
        return self.list_devices()


class AudioControlHandler(BaseHTTPRequestHandler):
    server_version = "HestiaAudioControl/1.0"

    def log_message(self, format, *args):
        LOG.debug(format, *args)

    def _send(self, status, data):
        raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _authorized(self):
        key = self.server.api_key
        provided = self.headers.get("X-Hestia-Key", "")
        if key and not hmac.compare_digest(provided, key):
            self._send(401, {"error": "Unauthorized"})
            return False
        return True

    def do_GET(self):
        if self.path != "/devices":
            return self._send(404, {"error": "Not found"})
        if not self._authorized():
            return
        try:
            self._send(200, self.server.control.list_devices())
        except Exception as exc:
            LOG.exception("Unable to list PortAudio devices")
            self._send(503, {"error": f"Audio device discovery failed: {exc}"})

    def do_PUT(self):
        if self.path != "/devices":
            return self._send(404, {"error": "Not found"})
        if not self._authorized():
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 1 <= length <= 256:
                raise ValueError("Invalid settings payload length")
            body = json.loads(self.rfile.read(length))
            result = self.server.control.update(body)
            self._send(200, result)
        except (ValueError, json.JSONDecodeError) as exc:
            self._send(400, {"error": str(exc)})
        except Exception:
            LOG.exception("Unable to persist audio device selection")
            self._send(500, {"error": "Unable to save audio device selection"})


class AudioControlServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, control: DeviceControl, api_key: str):
        super().__init__(address, AudioControlHandler)
        self.control = control
        self.api_key = api_key


def start_server(control: DeviceControl, *, port: int = 8766, api_key: str = ""):
    if not 1 <= port <= 65535:
        raise ValueError("LISTENER_CONTROL_PORT must be between 1 and 65535")
    server = AudioControlServer(("0.0.0.0", port), control, api_key)
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="audio-device-api")
    thread.start()
    LOG.info("Internal audio device control listening on port %d (Docker network only)", port)
    return server
