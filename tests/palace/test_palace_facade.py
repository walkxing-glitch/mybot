"""MemoryPalace facade round-trip: archive_session + assemble_context."""
from __future__ import annotations

import json
from pathlib import Path

from mybot.palace import MemoryPalace
from mybot.palace.config import PalaceConfig


FIXTURE = Path(__file__).parent / "fixtures" / "beijing_spending_session.json"


class _Reranker:
    def rerank(self, q, docs):
        return [1.0] * len(docs)


async def test_palace_round_trip(tmp_path, fake_llm, fake_embedder):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    session = json.loads(FIXTURE.read_text())
    fake_llm.responses = [
        # chunker pass
        json.dumps([{
            "msg_indices": [0, 1, 2],
            "drawer_topic": "北京消费",
            "summary": "用户问北京消费 69 万；要求别启动 myontology/backend",
            "keywords": ["北京", "消费"],
            "proposed_room_label": "消费",
        }]),
        # explicit extractor pass
        json.dumps([{
            "entry_type": "rule",
            "content": "别启动 myontology/backend",
        }]),
    ]
    palace = MemoryPalace(
        cfg=cfg, llm=fake_llm, embedder=fake_embedder, reranker=_Reranker(),
    )
    await palace.initialize()
    await palace.archive_session(
        "s1", session, now_date="2026-04-16", now_year=2026,
    )
    ctx = await palace.assemble_context(
        "北京花了多少", now_year=2026, now_date="2026-04-16",
    )
    assert "北京" in ctx
    assert "myontology/backend" in ctx
    assert "不可用" not in ctx
    await palace.close()


async def test_palace_stats(tmp_path, fake_llm, fake_embedder):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    palace = MemoryPalace(
        cfg=cfg, llm=fake_llm, embedder=fake_embedder, reranker=_Reranker(),
    )
    await palace.initialize()
    stats = await palace.get_stats()
    assert stats == {
        "north_drawers": 0, "south_drawers": 0,
        "atrium_active": 0, "atrium_pending": 0,
    }
    await palace.close()


async def test_palace_end_session_compat(tmp_path, fake_llm, fake_embedder):
    """MemoryEngine-compatible shim."""
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    fake_llm.responses = ["[]"]
    palace = MemoryPalace(
        cfg=cfg, llm=fake_llm, embedder=fake_embedder, reranker=_Reranker(),
    )
    result = await palace.end_session("s1", [])
    assert result["session_id"] == "s1"
    assert result["north_ids"] == []
    await palace.close()
