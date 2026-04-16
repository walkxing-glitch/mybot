"""PalaceTool smoke tests."""
from __future__ import annotations

import json
import uuid

from mybot.palace import MemoryPalace
from mybot.palace.config import PalaceConfig
from mybot.palace.tool_palace import PalaceTool


class _Reranker:
    def rerank(self, q, docs):
        return [1.0] * len(docs)


async def _build_palace(tmp_path, fake_llm, fake_embedder):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    palace = MemoryPalace(
        cfg=cfg, llm=fake_llm, embedder=fake_embedder, reranker=_Reranker(),
    )
    await palace.initialize()
    return palace


async def test_tool_get_raw_conversation(tmp_path, fake_llm, fake_embedder):
    fake_llm.responses = [json.dumps([{
        "msg_indices": [0, 1],
        "drawer_topic": "测试",
        "summary": "测试摘要",
        "keywords": [],
        "proposed_room_label": "消费",
    }])]
    palace = await _build_palace(tmp_path, fake_llm, fake_embedder)
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    result = await palace.archive_session(
        "s1", msgs, now_date="2026-04-16", now_year=2026,
    )
    nid = result.north_ids[0]

    tool = PalaceTool(palace=palace)
    tr = await tool.execute(operation="get_raw_conversation", drawer_id=nid)
    assert tr.success
    data = json.loads(tr.output)
    assert data["raw_messages"][0]["content"] == "hi"
    await palace.close()


async def test_tool_get_raw_by_south_id(tmp_path, fake_llm, fake_embedder):
    fake_llm.responses = [json.dumps([{
        "msg_indices": [0, 1],
        "drawer_topic": "测试",
        "summary": "测试摘要",
        "keywords": [],
        "proposed_room_label": "消费",
    }])]
    palace = await _build_palace(tmp_path, fake_llm, fake_embedder)
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    result = await palace.archive_session(
        "s1", msgs, now_date="2026-04-16", now_year=2026,
    )
    sid = result.south_ids[0]

    tool = PalaceTool(palace=palace)
    tr = await tool.execute(operation="get_raw_conversation", drawer_id=sid)
    assert tr.success
    data = json.loads(tr.output)
    assert data["south"]["summary"] == "测试摘要"
    assert data["north_messages"][0]["raw_messages"][0]["content"] == "hi"
    await palace.close()


async def test_tool_stats(tmp_path, fake_llm, fake_embedder):
    palace = await _build_palace(tmp_path, fake_llm, fake_embedder)
    tool = PalaceTool(palace=palace)
    tr = await tool.execute(operation="stats")
    assert tr.success
    stats = json.loads(tr.output)
    assert stats == {
        "north_drawers": 0, "south_drawers": 0,
        "atrium_active": 0, "atrium_pending": 0,
    }
    await palace.close()


async def test_tool_list_atrium(tmp_path, fake_llm, fake_embedder):
    palace = await _build_palace(tmp_path, fake_llm, fake_embedder)
    await palace.store.insert_atrium_entry(
        id=str(uuid.uuid4()), entry_type="rule",
        content="别启动 myontology/backend",
        source_type="explicit", status="active",
    )
    tool = PalaceTool(palace=palace)
    tr = await tool.execute(operation="list_atrium", entry_type="rule")
    assert tr.success
    entries = json.loads(tr.output)
    assert len(entries) == 1
    assert "myontology/backend" in entries[0]["content"]
    await palace.close()


async def test_tool_unknown_operation(tmp_path, fake_llm, fake_embedder):
    palace = await _build_palace(tmp_path, fake_llm, fake_embedder)
    tool = PalaceTool(palace=palace)
    tr = await tool.execute(operation="bogus")
    assert not tr.success
    assert "unknown" in (tr.error or "")
    await palace.close()


async def test_tool_missing_required(tmp_path, fake_llm, fake_embedder):
    palace = await _build_palace(tmp_path, fake_llm, fake_embedder)
    tool = PalaceTool(palace=palace)
    tr = await tool.execute(operation="get_raw_conversation")
    assert not tr.success
    assert "drawer_id" in (tr.error or "")
    await palace.close()
