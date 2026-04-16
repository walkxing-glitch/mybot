"""Retriever: RRF merge + end-to-end with Fake embedder/reranker."""
from __future__ import annotations

from mybot.palace.config import PalaceConfig
from mybot.palace.retriever import Retriever, _rrf_merge


def test_rrf_merge_basic():
    a = [{"drawer_id": "A"}, {"drawer_id": "B"}, {"drawer_id": "C"}]
    b = [{"drawer_id": "B"}, {"drawer_id": "D"}, {"drawer_id": "A"}]
    merged = _rrf_merge([a, b], k=60)
    ids = [m["drawer_id"] for m in merged]
    assert ids[0] in {"A", "B"}
    assert set(ids) == {"A", "B", "C", "D"}


class FakeReranker:
    def rerank(self, query, docs):
        return [1.0 if "hit" in d else 0.1 for d in docs]


async def test_retriever_end_to_end(store, fake_embedder):
    cfg = PalaceConfig(top_k_south=2)
    texts = ["hit: 北京消费", "上海工作", "hit: 北京吃饭"]
    for i, t in enumerate(texts):
        emb = fake_embedder.encode(t)[0]
        await store.insert_south_drawer(
            year=2026, floor=100 + i, room=1, drawer=1,
            date=f"2026-04-{10+i:02d}",
            north_ref_ids=[f"N-2026-{100+i:03d}-01-01"],
            room_type="fixed", room_label="消费",
            drawer_topic=t, summary=t, keywords=["t"],
            embedding=emb,
        )
    retr = Retriever(
        cfg=cfg, store=store,
        embedder=fake_embedder, reranker=FakeReranker(),
    )
    hits = await retr.search("北京", now_year=2026)
    assert len(hits) == 2
    for h in hits:
        assert "hit" in h["summary"]


async def test_retriever_empty_query(store, fake_embedder):
    retr = Retriever(
        cfg=PalaceConfig(), store=store,
        embedder=fake_embedder, reranker=FakeReranker(),
    )
    assert await retr.search("   ") == []


async def test_retriever_no_hits(store, fake_embedder):
    retr = Retriever(
        cfg=PalaceConfig(), store=store,
        embedder=fake_embedder, reranker=FakeReranker(),
    )
    assert await retr.search("nothing here", now_year=2026) == []
