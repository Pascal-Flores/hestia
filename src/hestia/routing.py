"""Example-based semantic routing, independent of the language model."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol, Sequence

from .manifest import Tool


class Embedder(Protocol):
    def encode(self, texts: Sequence[str], *, kind: str) -> list[list[float]]: ...


class SentenceTransformerEmbedder:
    """Optional local multilingual model; cached in memory for the process lifetime."""

    def __init__(self, model_name: str = "intfloat/multilingual-e5-small"):
        try:
            from sentence_transformers import SentenceTransformer
        except ImportError as exc:
            raise RuntimeError("Install embedding support: pip install '.[embeddings]'") from exc
        self.model = SentenceTransformer(model_name, device="cpu")
        self.e5 = "e5" in model_name.lower()

    def encode(self, texts: Sequence[str], *, kind: str) -> list[list[float]]:
        prefix = ("query: " if kind == "query" else "passage: ") if self.e5 else ""
        vectors = self.model.encode([prefix + t for t in texts], normalize_embeddings=True)
        return vectors.tolist()


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b):
        raise ValueError("Embedding dimension mismatch")
    norm = math.sqrt(sum(x * x for x in a) * sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / norm if norm else 0.0


@dataclass(frozen=True)
class Route:
    status: str  # matched, ambiguous, no_match
    tool_id: str | None
    candidates: tuple[tuple[str, float], ...]


class SemanticRouter:
    def __init__(self, tools: dict[str, Tool], embedder: Embedder, *,
                 min_score: float = 0.80, min_margin: float = 0.035):
        self.tools = tools
        self.embedder = embedder
        self.min_score = min_score
        self.min_margin = min_margin
        self.examples = [(tool.id, example) for tool in tools.values() for example in tool.examples]
        self.vectors = embedder.encode([example for _, example in self.examples], kind="passage") if self.examples else []
        if len(self.vectors) != len(self.examples):
            raise ValueError("Embedder returned wrong number of vectors")

    def route(self, message: str) -> Route:
        if not message.strip() or not self.examples:
            return Route("no_match", None, ())
        vectors = self.embedder.encode([message], kind="query")
        if len(vectors) != 1:
            raise ValueError("Embedder returned wrong number of vectors")
        scores: dict[str, float] = {}
        for (tool_id, _), vector in zip(self.examples, self.vectors):
            score = cosine(vectors[0], vector)
            scores[tool_id] = max(score, scores.get(tool_id, -1.0))
        ranked = sorted(scores.items(), key=lambda item: (-item[1], item[0]))
        top_id, top_score = ranked[0]
        limit = self.tools[top_id].threshold if self.tools[top_id].threshold is not None else self.min_score
        if top_score < limit:
            return Route("no_match", None, tuple(ranked[:3]))
        if len(ranked) > 1 and top_score - ranked[1][1] < self.min_margin:
            return Route("ambiguous", None, tuple(ranked[:3]))
        return Route("matched", top_id, tuple(ranked[:3]))
