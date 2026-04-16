"""Writer.archive_session end-to-end tests."""
from __future__ import annotations

import json
from pathlib import Path

from mybot.palace.config import PalaceConfig
from mybot.palace.writer import Writer


BEIJING_FIXTURE = Path(__file__).parent / "fixtures" / "beijing_spending_session.json"


async def test_archive_session_writes_north_south_atrium(
    store, fake_llm, fake_embedder,
):
    session = json.loads(BEIJING_FIXTURE.read_text())
    fake_llm.responses = [
        # chunker pass
        json.dumps([{
            "msg_indices": [0, 1, 2],
            "drawer_topic": "北京消费问答+规则",
            "summary": "用户问北京消费，算出 69 万元；并要求别启动 myontology/backend",
            "keywords": ["北京", "消费", "myontology"],
            "proposed_room_label": "消费",
        }]),
        # explicit extractor pass
        json.dumps([{
            "entry_type": "rule",
            "content": "别启动 myontology/backend",
        }]),
    ]
    w = Writer(
        cfg=PalaceConfig(), store=store, llm=fake_llm, embedder=fake_embedder,
    )
    result = await w.archive_session(
        session_id="t-1", messages=session,
        now_date="2026-04-16", now_year=2026,
    )
    assert len(result.north_ids) == 1
    assert len(result.south_ids) == 1
    assert len(result.atrium_ids) == 1

    entries = await store.list_atrium_entries(status="active")
    assert any("myontology/backend" in e["content"] for e in entries)


async def test_archive_session_blacklist_blocks_atrium(
    store, fake_llm, fake_embedder,
):
    """Failure narratives may land in 南塔 summary but must NEVER reach 中庭."""
    session = [
        {"role": "user", "content": "记住：我在北京花了多少钱"},
        {"role": "assistant", "content": "[ERROR] 本体论服务 8003 不可用"},
    ]
    fake_llm.responses = [
        # chunker: puts failure narrative into summary
        json.dumps([{
            "msg_indices": [0, 1],
            "drawer_topic": "查询失败",
            "summary": "用户问北京消费，但服务不可用",
            "keywords": ["失败"],
            "proposed_room_label": "杂项",
        }]),
        # explicit extractor: the user said 记住 about a fact that contains a
        # blacklisted phrase — the code-layer filter should drop it.
        json.dumps([{
            "entry_type": "fact",
            "content": "本体论服务 8003 不可用",
        }]),
    ]
    w = Writer(
        cfg=PalaceConfig(), store=store, llm=fake_llm, embedder=fake_embedder,
    )
    await w.archive_session(
        session_id="t-2", messages=session,
        now_date="2026-04-16", now_year=2026,
    )
    # "杂项" isn't a fixed label so Router opens a dynamic room (11-19).
    # Scan all possible rooms — the failure narrative should land somewhere.
    all_drawers = []
    for room in range(1, 21):
        all_drawers.extend(
            await store.list_room_south_drawers(date="2026-04-16", room=room)
        )
    assert any("不可用" in d["summary"] for d in all_drawers)
    entries = await store.list_atrium_entries()
    assert all("不可用" not in e["content"] for e in entries)


async def test_archive_session_no_chunks_short_circuits(
    store, fake_llm, fake_embedder,
):
    fake_llm.responses = ["[]"]
    w = Writer(
        cfg=PalaceConfig(), store=store, llm=fake_llm, embedder=fake_embedder,
    )
    result = await w.archive_session(
        session_id="t-3",
        messages=[{"role": "user", "content": "hi"}],
        now_date="2026-04-16", now_year=2026,
    )
    assert result.north_ids == []
    assert result.south_ids == []


async def test_archive_session_empty_messages(store, fake_llm, fake_embedder):
    w = Writer(
        cfg=PalaceConfig(), store=store, llm=fake_llm, embedder=fake_embedder,
    )
    result = await w.archive_session(
        session_id="t-4", messages=[],
        now_date="2026-04-16", now_year=2026,
    )
    assert result.north_ids == []
