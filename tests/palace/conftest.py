"""Shared fixtures for palace tests."""
from __future__ import annotations

import numpy as np
import pytest
import pytest_asyncio

from mybot.palace.config import PalaceConfig
from mybot.palace.store import PalaceStore


@pytest_asyncio.fixture
async def store(tmp_path):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    s = PalaceStore(cfg)
    await s.initialize()
    yield s
    await s.close()


class FakeLLM:
    """Scripted LLM: pops responses in order."""

    def __init__(self, responses=None):
        self.responses = list(responses or [])
        self.calls: list = []

    async def __call__(self, messages):
        self.calls.append(messages)
        if not self.responses:
            raise RuntimeError("FakeLLM out of responses")
        return self.responses.pop(0)


@pytest.fixture
def fake_llm():
    return FakeLLM()


class FakeEmbedder:
    """Deterministic embedding: hash(text) → 1024-dim unit vector."""

    def __init__(self, dim: int = 1024):
        self.dim = dim

    def encode(self, texts):
        if isinstance(texts, str):
            texts = [texts]
        out = []
        for t in texts:
            rng = np.random.default_rng(abs(hash(t)) % (2**32))
            v = rng.standard_normal(self.dim).astype("float32")
            v /= np.linalg.norm(v) + 1e-8
            out.append(v)
        return np.stack(out)


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()
