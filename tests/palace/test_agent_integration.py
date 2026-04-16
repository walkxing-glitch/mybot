"""Smoke tests: MemoryPalace imports and instantiates without errors."""
from __future__ import annotations


def test_import_chain():
    import mybot.palace  # noqa: F401
    from mybot.palace import MemoryPalace, PalaceConfig
    assert MemoryPalace is not None
    assert PalaceConfig is not None


def test_palace_has_memory_engine_shims():
    """MemoryPalace must expose the two methods Agent calls."""
    from mybot.palace import MemoryPalace
    assert hasattr(MemoryPalace, "get_context_for_prompt")
    assert hasattr(MemoryPalace, "end_session")
    assert hasattr(MemoryPalace, "initialize")


async def test_palace_instantiates(tmp_path, fake_llm, fake_embedder):
    from mybot.palace import MemoryPalace, PalaceConfig

    class _Reranker:
        def rerank(self, q, docs):
            return [1.0] * len(docs)

    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    palace = MemoryPalace(
        cfg=cfg, llm=fake_llm, embedder=fake_embedder, reranker=_Reranker(),
    )
    await palace.initialize()
    stats = await palace.get_stats()
    assert stats["north_drawers"] == 0
    await palace.close()
