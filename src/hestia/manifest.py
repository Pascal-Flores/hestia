"""Discover and validate declarative tool manifests."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

import yaml
from jsonschema import Draft202012Validator


_ID = re.compile(r"^[a-z][a-z0-9_.-]{1,79}$")
_STEP_ID = re.compile(r"^[a-z][a-z0-9_-]*$")
SUPPORTED_STEPS = {"ai.extract", "python.call", "ai.respond", "tool.call", "core.template"}


class ManifestError(ValueError):
    """Invalid or conflicting tool manifest."""


@dataclass(frozen=True)
class Tool:
    id: str
    name: str
    description: str
    examples: tuple[str, ...]
    input_schema: dict[str, Any]
    steps: tuple[dict[str, Any], ...]
    directory: Path
    threshold: float | None = None
    permissions: tuple[str, ...] = ()
    requires_confirmation: bool = False


def load_tools(root: str | Path) -> dict[str, Tool]:
    """Load all tools recursively from <root>/**/tool.yaml."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ManifestError(f"Tools directory not found: {root}")
    tools: dict[str, Tool] = {}
    for path in sorted(root.rglob("tool.yaml")):
        if not path.resolve().is_relative_to(root):
            raise ManifestError(f"Manifest escapes tools directory: {path}")
        try:
            data = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.YAMLError as exc:
            raise ManifestError(f"Invalid YAML in {path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ManifestError(f"Manifest must be a mapping: {path}")
        tool_id = data.get("id")
        if not isinstance(tool_id, str) or not _ID.fullmatch(tool_id):
            raise ManifestError(f"Invalid tool id in {path}: {tool_id!r}")
        if tool_id in tools:
            raise ManifestError(f"Duplicate tool id: {tool_id}")
        routing = data.get("routing")
        if not isinstance(routing, dict):
            raise ManifestError(f"{tool_id}: routing must be a mapping")
        examples = routing.get("examples")
        if not isinstance(examples, list) or not examples or not all(
            isinstance(s, str) and s.strip() for s in examples
        ):
            raise ManifestError(f"{tool_id}: routing.examples must contain phrases")
        threshold = routing.get("threshold")
        if threshold is not None and (
            isinstance(threshold, bool) or not isinstance(threshold, (int, float))
            or not -1 <= threshold <= 1
        ):
            raise ManifestError(f"{tool_id}: invalid routing.threshold")
        inputs = data.get("inputs", {"type": "object", "properties": {}})
        if not isinstance(inputs, dict) or inputs.get("type") != "object":
            raise ManifestError(f"{tool_id}: inputs must be an object JSON Schema")
        try:
            Draft202012Validator.check_schema(inputs)
        except Exception as exc:
            raise ManifestError(f"{tool_id}: invalid inputs JSON Schema: {exc}") from exc
        steps = data.get("steps")
        if not isinstance(steps, list) or not steps:
            raise ManifestError(f"{tool_id}: steps must be a nonempty list")
        seen: set[str] = set()
        for step in steps:
            if not isinstance(step, dict):
                raise ManifestError(f"{tool_id}: step must be a mapping")
            sid, uses = step.get("id"), step.get("uses")
            if not isinstance(sid, str) or not _STEP_ID.fullmatch(sid) or sid in seen:
                raise ManifestError(f"{tool_id}: invalid or duplicate step id: {sid}")
            seen.add(sid)
            if uses not in SUPPORTED_STEPS:
                raise ManifestError(f"{tool_id}: unsupported step type {uses}")
            if not isinstance(step.get("with", {}), dict):
                raise ManifestError(f"{tool_id}: step {sid}.with must be a mapping")
            if uses == "python.call":
                entry = step.get("with", {}).get("entrypoint")
                if not isinstance(entry, str) or not re.fullmatch(r"[A-Za-z_][\w-]*\.py:[A-Za-z_]\w*", entry):
                    raise ManifestError(f"{tool_id}: unsafe python entrypoint: {entry}")
                script = (path.parent / entry.split(":", 1)[0]).resolve()
                if not script.is_relative_to(path.parent.resolve()) or not script.is_file():
                    raise ManifestError(f"{tool_id}: missing or unsafe script: {entry}")
        permissions = data.get("permissions", [])
        if not isinstance(permissions, list) or not all(isinstance(p, str) for p in permissions):
            raise ManifestError(f"{tool_id}: permissions must be a list of strings")
        if not isinstance(data.get("requires_confirmation", False), bool):
            raise ManifestError(f"{tool_id}: requires_confirmation must be boolean")
        name = data.get("name", tool_id)
        description = data.get("description", "")
        if not isinstance(name, str) or not isinstance(description, str):
            raise ManifestError(f"{tool_id}: name/description must be strings")
        tools[tool_id] = Tool(
            id=tool_id, name=name, description=description,
            examples=tuple(examples), input_schema=inputs, steps=tuple(steps),
            directory=path.parent.resolve(), threshold=float(threshold) if threshold is not None else None,
            permissions=tuple(permissions), requires_confirmation=data.get("requires_confirmation", False),
        )
    return tools
