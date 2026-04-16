"""Embedder contract tests. Real model load marked slow."""
from __future__ import annotations

import pytest


def test_embedder_interface_contract():
    from mybot.palace.embedder import Embedder
    assert hasattr(Embedder, "encode")
    e = Embedder()
    assert e.dim == 1024
    assert e.model_name == "BAAI/bge-m3"


@pytest.mark.slow
def test_embedder_bge_m3_smoke():
    from mybot.palace.embedder import Embedder
    e = Embedder(model_name="BAAI/bge-m3", dim=1024)
    v = e.encode("北京消费")
    assert v.shape == (1, 1024)
    v2 = e.encode(["北京消费", "上海工作"])
    assert v2.shape == (2, 1024)
