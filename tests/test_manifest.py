from pathlib import Path

import pytest

from hestia.manifest import ManifestError, load_tools


TOOLS = Path(__file__).resolve().parents[1] / "tools"


def test_discovers_example_tools():
    tools = load_tools(TOOLS)
    assert set(tools) == {"math.add", "system.time", "system.uptime"}
    assert len(tools["math.add"].examples) >= 3
    assert tools["math.add"].steps[0]["uses"] == "ai.extract"


def test_rejects_duplicate_ids(tmp_path):
    content = (TOOLS / "system/time/tool.yaml").read_text(encoding="utf8")
    for dirname in ("a", "b"):
        folder = tmp_path / dirname
        folder.mkdir()
        (folder / "tool.yaml").write_text(content, encoding="utf8")
        (folder / "main.py").write_text("def run(a, b): return {}", encoding="utf8")
    with pytest.raises(ManifestError, match="Duplicate"):
        load_tools(tmp_path)


def test_rejects_entrypoint_escape(tmp_path):
    folder = tmp_path / "unsafe"
    folder.mkdir()
    content = (TOOLS / "system/time/tool.yaml").read_text(encoding="utf8")
    content = content.replace("main.py:run", "../danger.py:run")
    (folder / "tool.yaml").write_text(content, encoding="utf8")
    with pytest.raises(ManifestError, match="unsafe python entrypoint"):
        load_tools(tmp_path)
