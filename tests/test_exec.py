from pathlib import Path
import pytest

from hestia.manifest import ManifestError, load_tools
from hestia.routing import SemanticRouter
from hestia.runtime import WorkflowEngine, WorkflowFailure
from test_routing import ToyEmbedder
from test_runtime import ScriptedLLM

ROOT = Path(__file__).resolve().parents[1] / 'tools'


def run_explicit(tools, name, params=None):
    engine = WorkflowEngine(tools, SemanticRouter(tools, ToyEmbedder(), min_score=0), ScriptedLLM())
    return engine._run_tool(name, '', confirmed=False, context={}, stack=(), preset=params)


def test_shell_tool_runs_and_returns_json():
    tools = load_tools(ROOT)
    result = run_explicit(tools, 'system.uptime', {})
    assert result.status == 'completed'
    assert result.trace[0]['uses'] == 'exec.call'
    assert isinstance(result.trace[0]['output']['seconds'], int)
    assert 'secondes' in result.text


def test_exec_python_script_receives_json(tmp_path):
    d = tmp_path / 'test'; d.mkdir()
    (d/'main.py').write_text('''import json,sys
payload=json.load(sys.stdin)
print(json.dumps({'result': payload['parameters']['a'] + 1}))
''')
    (d/'tool.yaml').write_text('''id: example.python
name: Python tool
routing: {examples: ["add one to the value"]}
inputs:
  type: object
  properties: {a: {type: integer}}
  required: [a]
steps:
  - id: execute
    uses: exec.call
    with: {command: python3, args: [main.py], arguments: {a: 41}}
  - id: respond
    uses: core.template
    with:
      data: ${{ steps.execute.output }}
      template: "Résultat {{ result }}"
''')
    result = run_explicit(load_tools(tmp_path), 'example.python')
    assert result.status == 'completed' and result.text == 'Résultat 42'


def test_exec_rejects_path_escape(tmp_path):
    d = tmp_path / 'demo'; d.mkdir()
    (d/'tool.yaml').write_text('''id: example.escape
routing: {examples: ["escape"]}
steps:
  - id: execute
    uses: exec.call
    with: {command: ../unsafe, arguments: {}}
''')
    with pytest.raises(ManifestError, match='outside tool'):
        load_tools(tmp_path)


def test_exec_invalid_output_is_refused(tmp_path):
    d = tmp_path / 'demo'; d.mkdir()
    (d/'main.sh').write_text('echo not-json\n')
    (d/'tool.yaml').write_text('''id: example.bad
routing: {examples: ["bad"]}
steps:
  - id: execute
    uses: exec.call
    with: {command: sh, args: [main.sh], arguments: {}}
''')
    tools = load_tools(tmp_path)
    with pytest.raises(WorkflowFailure, match='JSON'):
        WorkflowEngine._exec_call(tools['example.bad'], 'sh', ['main.sh'], {}, {}, timeout=5, env_names=[])
