from dataclasses import replace

import pytest
import json
from pathlib import Path

from hestia.manifest import load_tools
from hestia.routing import SemanticRouter
from hestia.runtime import WorkflowEngine, WorkflowFailure

from test_routing import ToyEmbedder


TOOLS = Path(__file__).resolve().parents[1] / "tools"


class ScriptedLLM:
    def __init__(self, *answers):
        self.answers = list(answers)
        self.calls = []

    def complete(self, *, system, user, json_mode=False):
        self.calls.append((system, user, json_mode))
        assert self.answers, "Unexpected extra LLM call"
        return self.answers.pop(0)


def engine(*answers, tools=None, **options):
    tools = tools or load_tools(TOOLS)
    llm = ScriptedLLM(*answers)
    router = SemanticRouter(tools, ToyEmbedder(), min_score=0.7)
    return WorkflowEngine(tools, router, llm, **options), llm


def test_complete_extract_python_respond():
    extract = json.dumps({"status": "ready", "parameters": {"a": 4, "b": 8}})
    instance, model = engine(extract, "Cela fait douze.")
    result = instance.handle("Combien font 4 plus 8 ?")
    assert result.status == "completed"
    assert result.text == "Cela fait douze."
    assert [step["uses"] for step in result.trace] == ["ai.extract", "python.call", "ai.respond"]
    assert result.trace[1]["output"]["result"] == 12
    assert len(model.calls) == 2
    assert model.calls[0][2] is True
    assert model.calls[1][2] is False


def test_missing_parameters_prevents_python_call():
    missing = json.dumps({"status": "missing_parameters", "question": "Quel second nombre ?"})
    instance, _ = engine(missing)
    result = instance.handle("Fais une addition")
    assert result.status == "clarification"
    assert result.text == "Quel second nombre ?"
    assert result.trace == []


def test_wrong_types_block_execution():
    bad = json.dumps({"status": "ready", "parameters": {"a": "quatre", "b": 8}})
    instance, _ = engine(bad)
    result = instance.handle("Additionne 4 et 8")
    assert result.status == "error"
    assert "Paramètres invalides" in result.text


def test_not_applicable():
    instance, _ = engine('{"status":"not_applicable"}')
    assert instance.handle("Fais une addition").status == "not_applicable"


def test_no_tool_does_not_call_llm():
    instance, model = engine()
    assert instance.handle("Raconte l'histoire de la conquête spatiale").status == "no_tool"
    assert model.calls == []


def test_fallback_llm_for_unmatched_queries():
    other = ScriptedLLM("La réponse distante.")
    instance, _ = engine(fallback_llm=other)
    result = instance.handle("Raconte l'histoire de la conquête spatiale")
    assert result.status == "conversation"
    assert result.text == "La réponse distante."
    assert len(other.calls) == 1


def test_template_skips_llm():
    instance, model = engine()
    result = instance.handle("Quelle heure est-il ?")
    assert result.status == "completed"
    assert result.text.startswith("Il est ")
    assert model.calls == []


def test_permissions_and_confirmation():
    tools = load_tools(TOOLS)
    tools["math.add"] = replace(tools["math.add"], permissions=("math.write",), requires_confirmation=True)
    instance, _ = engine(tools=tools)
    assert instance.handle("Combien font 4 plus 8 ?").status == "permission_denied"
    instance, _ = engine(tools=tools, allowed_permissions=frozenset({"math.write"}))
    assert instance.handle("Combien font 4 plus 8 ?").status == "confirmation_required"


def test_nested_tool_call(tmp_path):
    tools = load_tools(TOOLS)
    tools_dir = tmp_path / "combo"
    tools_dir.mkdir()
    (tools_dir / "tool.yaml").write_text('''
id: math.combo
name: Somme composée
description: Appelle un autre outil
routing:
  examples: ["somme composée"]
inputs:
  type: object
  properties: {}
steps:
  - id: run
    uses: tool.call
    with:
      tool: math.add
      arguments: {a: 2, b: 5}
  - id: display
    uses: core.template
    with:
      data: ${{ steps.run.output }}
      template: "Somme : {{ result }}"
''', encoding="utf-8")
    tools.update(load_tools(tmp_path))
    instance, model = engine(tools=tools)
    # Call the composite tool directly to exercise nesting, independent of the toy embedder.
    result = instance._run_tool("math.combo", "somme composée", confirmed=False, context={}, stack=(), preset=None)
    assert result.status == "completed"
    assert result.text == "Somme : 7"
    assert model.calls == []


def test_reference_resolution_and_cycle_detection():
    assert WorkflowEngine._resolve("${{ steps.a.output.x }}", {"a": {"output": {"x": 2}}}) == 2
    instance, _ = engine()
    with pytest.raises(WorkflowFailure, match="cyclique"):
        instance._run_tool("math.add", "", confirmed=False, context={},
                           stack=("math.add",), preset={"a": 1, "b": 2})
