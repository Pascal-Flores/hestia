"""Validate local E5 prefix handling without downloading ONNX weights."""

import sys
import types

from hestia.routing import FastEmbedder


def test_fastembed_prefixes(monkeypatch):
    observed = []

    class Vector:
        def __init__(self, values):
            self.values = values

        def tolist(self):
            return self.values

    class FakeTextEmbedding:
        @classmethod
        def list_supported_models(cls):
            return [{"model": "intfloat/multilingual-e5-small"}]

        def __init__(self, **kwargs):
            observed.append(kwargs)

        def embed(self, texts):
            observed.append(texts)
            return (Vector([1.0, 0.0]) for _ in texts)

    monkeypatch.setitem(sys.modules, "fastembed", types.SimpleNamespace(TextEmbedding=FakeTextEmbedding))
    e = FastEmbedder("intfloat/multilingual-e5-small", cache_dir="/cache/embeddings")
    assert e.encode(["ouvre la porte"], kind="query") == [[1.0, 0.0]]
    assert e.encode(["ouvrir la porte"], kind="passage") == [[1.0, 0.0]]
    assert observed == [
        {"model_name": "intfloat/multilingual-e5-small", "cache_dir": "/cache/embeddings"},
        ["query: ouvre la porte"],
        ["passage: ouvrir la porte"],
    ]
