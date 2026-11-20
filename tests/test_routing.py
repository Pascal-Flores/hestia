from pathlib import Path

from hestia.manifest import load_tools
from hestia.routing import SemanticRouter


class ToyEmbedder:
    """Deterministic vectors, so CI never downloads model weights."""

    def encode(self, texts, *, kind):
        result = []
        for text in texts:
            text = text.lower()
            if any(word in text for word in ("heure", "heure", "actuelle")):
                result.append([0.0, 1.0])
            elif any(word in text for word in ("somme", "plus", "addition", "calcule", "calcul")):
                result.append([1.0, 0.0])
            elif "entre les deux" in text:
                result.append([1.0, 1.0])
            else:
                result.append([0.0, 0.0])
        return result


def sample_tools():
    # Legacy unit tests isolate three original plugins. The new catalog has
    # overlapping semantic examples and would intentionally be ambiguous.
    loaded = load_tools(Path(__file__).resolve().parents[1] / "tools")
    return {key: value for key, value in loaded.items()
            if key in {"math.add", "system.time", "system.uptime"}}


def test_retrieval_by_examples():
    router = SemanticRouter(sample_tools(), ToyEmbedder(), min_score=0.7)
    assert router.route("Combien font 3 plus 6").tool_id == "math.add"
    assert router.route("Dis-moi l'heure actuelle").tool_id == "system.time"


def test_refuses_unrelated_queries():
    router = SemanticRouter(sample_tools(), ToyEmbedder(), min_score=0.7)
    assert router.route("Quel temps fait-il demain ?").status == "no_match"
    assert router.route("").status == "no_match"


def test_ambiguous_requests_not_guessed():
    router = SemanticRouter(sample_tools(), ToyEmbedder(), min_score=0.7, min_margin=0.1)
    assert router.route("entre les deux").status == "ambiguous"
