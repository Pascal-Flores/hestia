"""Deterministic workflow runner. The LLM never chooses or executes a tool."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

from jsonschema import ValidationError, validate

from .llm import LLM
from .manifest import Tool
from .routing import Route, SemanticRouter


_REF = re.compile(r"^\$\{\{\s*steps\.([a-z][a-z0-9_-]*)\.output((?:\.[A-Za-z_][\w-]*)*)\s*\}\}$")
_RUNNER = r'''
import importlib.util, json, sys
script, func = sys.argv[1], sys.argv[2]
spec = importlib.util.spec_from_file_location("hestia_plugin", script)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
payload = json.load(sys.stdin)
result = getattr(module, func)(payload["parameters"], payload.get("context", {}))
print(json.dumps(result, ensure_ascii=False))
'''


@dataclass
class Outcome:
    status: str
    text: str
    tool_id: str | None = None
    output: Any = None
    trace: list[dict[str, Any]] = field(default_factory=list)
    route: Route | None = None


class WorkflowFailure(Exception):
    pass


class WorkflowEngine:
    def __init__(self, tools: dict[str, Tool], router: SemanticRouter, llm: LLM,
                 *, allowed_permissions: frozenset[str] = frozenset(),
                 fallback_llm: LLM | None = None, max_depth: int = 4):
        self.tools, self.router, self.llm = tools, router, llm
        self.allowed_permissions = allowed_permissions
        self.fallback_llm = fallback_llm
        self.max_depth = max_depth

    def handle(self, message: str, *, confirmed: bool = False,
               context: dict[str, Any] | None = None) -> Outcome:
        route = self.router.route(message)
        if route.status == "no_match":
            if self.fallback_llm is not None:
                reply = self.fallback_llm.complete(
                    system="Tu es Hestia, un assistant domestique. Réponds en français. "
                           "Tu n'as exécuté aucune action domotique.", user=message)
                return Outcome("conversation", reply, route=route)
            return Outcome("no_tool", "Aucun outil ne correspond suffisamment à cette demande.", route=route)
        if route.status == "ambiguous":
            names = ", ".join(self.tools[tid].name for tid, _ in route.candidates[:2])
            return Outcome("ambiguous", f"Peux-tu préciser ? J'hésite entre : {names}.", route=route)
        try:
            outcome = self._run_tool(route.tool_id, message, confirmed=confirmed,
                                     context=context or {}, stack=(), preset=None)
            outcome.route = route
            return outcome
        except (WorkflowFailure, ValidationError, ValueError, RuntimeError, OSError) as exc:
            return Outcome("error", f"L'outil n'a pas pu être exécuté : {exc}",
                           tool_id=route.tool_id, route=route)

    def _run_tool(self, tool_id: str, message: str, *, confirmed: bool,
                  context: dict[str, Any], stack: tuple[str, ...],
                  preset: dict[str, Any] | None, include_response: bool = True) -> Outcome:
        if tool_id not in self.tools:
            raise WorkflowFailure(f"Outil inconnu : {tool_id}")
        if tool_id in stack or len(stack) >= self.max_depth:
            raise WorkflowFailure("Appel cyclique ou profondeur maximale atteinte")
        tool = self.tools[tool_id]
        denied = set(tool.permissions) - self.allowed_permissions
        if denied:
            return Outcome("permission_denied", "Permission requise : " + ", ".join(sorted(denied)), tool_id)
        if tool.requires_confirmation and not confirmed:
            return Outcome("confirmation_required", f"Confirmer l'exécution de « {tool.name} » ?", tool_id)
        trace: list[dict[str, Any]] = []
        outputs: dict[str, dict[str, Any]] = {}
        latest: Any = preset if preset is not None else {}
        if preset is not None:
            validate(preset, tool.input_schema)
        for step in tool.steps:
            sid, uses = step["id"], step["uses"]
            opts = self._resolve(step.get("with", {}), outputs)
            if uses == "ai.extract":
                if preset is not None:
                    result = preset
                else:
                    prompt = str(opts.get("prompt", ""))
                    raw = self.llm.complete(
                        system=("Tu extrais les paramètres d'un seul outil. Ne sélectionne pas d'autre outil. "
                                "Réponds UNIQUEMENT par un objet JSON : "
                                '{"status":"ready","parameters":{...}} ou '
                                '{"status":"missing_parameters","question":"..."} ou '
                                "{\"status\":\"not_applicable\"}. N'invente pas de valeurs.\n"
                                f"Outil : {tool.description}\nRègles : {prompt}\n"
                                f"Schéma JSON des paramètres : {json.dumps(tool.input_schema, ensure_ascii=False)}"),
                        user=json.dumps({"message": message, "context": context}, ensure_ascii=False),
                        json_mode=True,
                    )
                    try:
                        parsed = json.loads(raw)
                    except json.JSONDecodeError as exc:
                        raise WorkflowFailure(f"Réponse JSON invalide de l'extracteur : {exc}") from exc
                    if not isinstance(parsed, dict):
                        raise WorkflowFailure("L'extracteur n'a pas renvoyé d'objet")
                    status = parsed.get("status")
                    if status == "missing_parameters":
                        question = parsed.get("question")
                        if not isinstance(question, str) or not question.strip():
                            question = "Il me manque des informations pour effectuer cette action."
                        return Outcome("clarification", question, tool_id, trace=trace)
                    if status == "not_applicable":
                        return Outcome("not_applicable", "Cette demande ne correspond pas à l'outil sélectionné.", tool_id, trace=trace)
                    if status != "ready" or not isinstance(parsed.get("parameters"), dict):
                        raise WorkflowFailure("Format d'extraction incorrect")
                    result = parsed["parameters"]
                try:
                    validate(result, tool.input_schema)
                except ValidationError as exc:
                    raise WorkflowFailure(f"Paramètres invalides : {exc.message}") from exc
            elif uses == "python.call":
                args = opts.get("arguments", latest)
                if not isinstance(args, dict):
                    raise WorkflowFailure(f"Arguments du step {sid} non structurés")
                result = self._python_call(tool, str(opts["entrypoint"]), args, context,
                                           timeout=int(opts.get("timeout", 15)),
                                           env_names=opts.get("env", []))
            elif uses == "tool.call":
                target = opts.get("tool")
                args = opts.get("arguments", latest)
                if not isinstance(target, str) or not isinstance(args, dict):
                    raise WorkflowFailure("tool.call nécessite tool et arguments (objet)")
                nested = self._run_tool(target, message, confirmed=confirmed, context=context,
                                        stack=stack + (tool_id,), preset=args,
                                        include_response=False)
                if nested.status != "completed":
                    nested.trace = trace + nested.trace
                    return nested
                result = nested.output
                trace.extend(nested.trace)
            elif uses == "ai.respond":
                if not include_response:
                    continue
                data = opts.get("data", latest)
                reply = self.llm.complete(
                    system=("Tu es Hestia. Formule une réponse courte et naturelle en français, "
                            "uniquement à partir du résultat de l'outil. "
                            "Ne prétends jamais qu'une action a réussi si le résultat ne le confirme pas. "
                            f"Instructions de l'outil : {opts.get('prompt', '')}"),
                    user=json.dumps({"request": message, "tool_result": data}, ensure_ascii=False),
                )
                result = {"text": reply}
            elif uses == "core.template":
                data = opts.get("data", latest)
                if not isinstance(data, dict):
                    raise WorkflowFailure("core.template attend un objet")
                template = opts.get("template", "")
                if not isinstance(template, str):
                    raise WorkflowFailure("Template invalide")
                result = {"text": _template(template, data)}
            else:
                raise WorkflowFailure(f"Step inconnu : {uses}")
            outputs[sid] = {"output": result}
            latest = result
            trace.append({"step": sid, "uses": uses, "output": result})
        text = latest.get("text") if isinstance(latest, dict) else None
        if not isinstance(text, str):
            text = "L'outil a terminé son exécution."
        return Outcome("completed", text, tool_id=tool_id, output=latest, trace=trace)

    @staticmethod
    def _resolve(value: Any, outputs: dict[str, dict]) -> Any:
        if isinstance(value, str):
            match = _REF.fullmatch(value)
            if not match:
                if "${{" in value:
                    raise WorkflowFailure("Les références doivent occuper toute la valeur")
                return value
            sid, path = match.group(1), match.group(2)
            if sid not in outputs:
                raise WorkflowFailure(f"Référence au step inconnu ou futur : {sid}")
            result = outputs[sid]["output"]
            for part in filter(None, path.split(".")):
                if not isinstance(result, dict) or part not in result:
                    raise WorkflowFailure(f"Référence introuvable : {sid}.{part}")
                result = result[part]
            return result
        if isinstance(value, dict):
            return {k: WorkflowEngine._resolve(v, outputs) for k, v in value.items()}
        if isinstance(value, list):
            return [WorkflowEngine._resolve(v, outputs) for v in value]
        return value

    @staticmethod
    def _python_call(tool: Tool, entrypoint: str, args: dict, context: dict,
                     *, timeout: int, env_names: list[str]) -> Any:
        if not re.fullmatch(r"[A-Za-z_][\w-]*\.py:[A-Za-z_]\w*", entrypoint):
            raise WorkflowFailure("Entrypoint non autorisé")
        script, func = entrypoint.split(":", 1)
        path = (tool.directory / script).resolve()
        if not path.is_relative_to(tool.directory) or not path.is_file():
            raise WorkflowFailure("Script introuvable ou hors du dossier de l'outil")
        if not isinstance(env_names, list) or not all(isinstance(x, str) for x in env_names):
            raise WorkflowFailure("Liste de variables d'environnement invalide")
        if not 1 <= timeout <= 120:
            raise WorkflowFailure("Timeout hors limites")
        env = {"PYTHONIOENCODING": "utf-8"}
        env.update({name: os.environ[name] for name in env_names if name in os.environ})
        try:
            proc = subprocess.run(
                [sys.executable, "-I", "-c", _RUNNER, str(path), func],
                input=json.dumps({"parameters": args, "context": context}, ensure_ascii=False),
                text=True, capture_output=True, timeout=timeout, cwd=str(tool.directory), env=env,
                check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise WorkflowFailure("Délai d'exécution du script dépassé") from exc
        if proc.returncode != 0:
            raise WorkflowFailure(f"Le script a échoué (code {proc.returncode}) : {proc.stderr[-500:]}")
        if len(proc.stdout) > 500_000:
            raise WorkflowFailure("Réponse du script trop volumineuse")
        try:
            return json.loads(proc.stdout)
        except json.JSONDecodeError as exc:
            raise WorkflowFailure("Le script doit retourner une seule valeur JSON sur stdout") from exc


def _template(template: str, data: dict[str, Any]) -> str:
    """Simple {{ key }} replacement, no expression evaluation."""
    def substitute(match: re.Match) -> str:
        key = match.group(1)
        if key not in data:
            raise WorkflowFailure(f"Variable de template manquante : {key}")
        return str(data[key])
    return re.sub(r"\{\{\s*([a-zA-Z_]\w*)\s*\}\}", substitute, template)
