"""Reranker contract tests."""
from __future__ import annotations


def test_reranker_interface():
    from mybot.palace.reranker import Reranker
    assert hasattr(Reranker, "rerank")
    r = Reranker()
    assert r.model_name == "BAAI/bge-reranker-v2-m3"
    # empty docs short-circuits without loading model
    assert r.rerank("q", []) == []
