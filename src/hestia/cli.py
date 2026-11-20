"""Text-first CLI, with optional recorded-audio input/output."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys

from .llm import OpenAICompatibleLLM
from .manifest import ManifestError, load_tools
from .routing import SemanticRouter, SentenceTransformerEmbedder
from .runtime import WorkflowEngine


def _default_tools() -> Path:
    return Path(os.environ.get("HESTIA_TOOLS_DIR", Path(__file__).resolve().parents[2] / "tools"))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Hestia: local semantic workflow assistant")
    parser.add_argument("--tools-dir", type=Path, default=_default_tools())
    parser.add_argument("--min-score", type=float, default=float(os.getenv("HESTIA_ROUTE_MIN_SCORE", "0.80")))
    parser.add_argument("--min-margin", type=float, default=float(os.getenv("HESTIA_ROUTE_MIN_MARGIN", "0.035")))
    parser.add_argument("--embedding-model", default=os.getenv("HESTIA_EMBEDDING_MODEL", "intfloat/multilingual-e5-small"))
    parser.add_argument("--allow", action="append", default=[], help="Permission granted to installed tools (repeatable)")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("tools", help="List validated tool manifests")
    route_parser = commands.add_parser("route", help="Show semantic candidates without executing")
    route_parser.add_argument("text")
    ask_parser = commands.add_parser("ask", help="Run the full text workflow")
    ask_parser.add_argument("text")
    ask_parser.add_argument("--debug", action="store_true")
    voice_parser = commands.add_parser("voice-file", help="Transcribe WAV, run workflow, synthesize response")
    voice_parser.add_argument("--input", required=True, type=Path)
    voice_parser.add_argument("--output", required=True, type=Path)
    voice_parser.add_argument("--whisper-model", default="tiny")
    voice_parser.add_argument("--tts-language", default="french")
    voice_parser.add_argument("--voice", default=None)
    voice_parser.add_argument("--profile", choices=["natural", "mii"], default="natural")
    args = parser.parse_args(argv)

    try:
        tools = load_tools(args.tools_dir)
        if args.command == "tools":
            for tool in tools.values():
                print(f"{tool.id} — {tool.name} ({len(tool.examples)} exemples)")
            return 0
        if not tools:
            raise RuntimeError("No tool manifests found")
        embedder = SentenceTransformerEmbedder(args.embedding_model)
        router = SemanticRouter(tools, embedder, min_score=args.min_score, min_margin=args.min_margin)
        if args.command == "route":
            selection = router.route(args.text)
            print(f"Status: {selection.status}; tool: {selection.tool_id}")
            for tool_id, score in selection.candidates:
                print(f"  {score:.4f} {tool_id}")
            return 0
        fallback = (OpenAICompatibleLLM.from_env("HESTIA_FALLBACK_LLM")
                    if os.getenv("HESTIA_FALLBACK_LLM_URL") else None)
        engine = WorkflowEngine(tools, router, OpenAICompatibleLLM.from_env(),
                                allowed_permissions=frozenset(args.allow), fallback_llm=fallback)
        if args.command == "voice-file":
            from .audio import transcribe, synthesize
            text = transcribe(args.input, model_name=args.whisper_model)
            print("Utilisateur :", text)
            if not text:
                print("Transcription vide", file=sys.stderr)
                return 2
            outcome = engine.handle(text)
            print(f"Hestia [{outcome.status}] : {outcome.text}")
            synthesize(outcome.text, args.output, language=args.tts_language,
                       voice=args.voice, profile=args.profile)
            print("Audio :", args.output)
            return 0 if outcome.status in ("completed", "conversation") else 2
        outcome = engine.handle(args.text)
        if outcome.status == "confirmation_required" and sys.stdin.isatty():
            accepted = input(outcome.text + " [o/N] ").strip().lower() in ("o", "oui")
            if accepted:
                outcome = engine.handle(args.text, confirmed=True)
        print(f"Hestia [{outcome.status}] : {outcome.text}")
        if args.debug:
            print("Route :", outcome.route)
            for item in outcome.trace:
                print(item)
        return 0 if outcome.status in ("completed", "conversation") else 2
    except (ManifestError, RuntimeError, OSError, ValueError) as exc:
        print(f"Erreur : {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
