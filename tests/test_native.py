"""Native launcher contract tests without models or external network."""
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
LAUNCH = ROOT / "scripts/native/run.sh"


def run_native(profile="2g"):
    return subprocess.run([str(LAUNCH), "--dry-run"], text=True, capture_output=True,
                          env={**os.environ, "HESTIA_MEMORY_PROFILE": profile}, timeout=10)


def test_memory_profiles_select_model_and_context():
    output = run_native("2g")
    assert output.returncode == 0, output.stderr
    assert "profile=2g" in output.stdout
    assert "0.6B" in output.stdout
    assert "context=1024" in output.stdout
    output = run_native("8g")
    assert output.returncode == 0, output.stderr
    assert "1.7B" in output.stdout
    assert "context=4096" in output.stdout


def test_unknown_memory_profile_is_an_error():
    result = run_native("123g")
    assert result.returncode != 0
    assert "HESTIA_MEMORY_PROFILE" in result.stderr


def test_native_does_not_need_binaries_for_dry_run():
    """The dependency check is explicit; --dry-run is useful before installation."""
    dry = run_native("4g")
    assert dry.returncode == 0
    assert "Processes: llama-server, whisper-server, pocket-tts, uvicorn" in dry.stdout
