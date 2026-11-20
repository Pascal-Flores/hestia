"""Non-network tests for the expanded plugin catalog."""
import json
import subprocess
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1] / "tools"

def test_calculator_evaluates_safe_expression():
    path = ROOT / "math" / "calculate" / "main.py"
    p = subprocess.run([sys.executable, str(path)], input=json.dumps({
        "parameters": {"expression": "2*(3+4)^2"}, "context": {}
    }), text=True, capture_output=True, timeout=5, check=True)
    result = json.loads(p.stdout)
    assert result["success"] is True
    assert result["result"] == 98

def test_calculator_rejects_code_execution():
    path = ROOT / "math" / "calculate" / "main.py"
    p = subprocess.run([sys.executable, str(path)], input=json.dumps({
        "parameters": {"expression": "__import__('os').system('id')"}, "context": {}
    }), text=True, capture_output=True, timeout=5, check=True)
    assert json.loads(p.stdout)["success"] is False

def test_plugins_have_manifest_and_execution_steps():
    listed = list(ROOT.glob("**/tool.yaml"))
    assert len(listed) >= 12
    for path in listed:
        data = yaml.safe_load(path.read_text())
        assert data["id"] and data["routing"]["examples"]
        assert len(data["steps"]) > 0
        for step in data["steps"]:
            if step["uses"] == "exec.call":
                command = step["with"]["command"]
                args = step["with"].get("argv", step["with"].get("args", []))
                assert command in {"python3", "bash", "sh"}
                assert (path.parent / args[0]).is_file()
