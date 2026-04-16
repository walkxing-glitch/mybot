"""Chunker tests with a scripted FakeLLM."""
from __future__ import annotations

import json
from pathlib import Path


FIXTURE = Path(__file__).parent / "fixtures" / "multi_topic_session.json"


async def test_chunker_basic(fake_llm):
    from mybot.palace.chunker import Chunker

    session = json.loads(FIXTURE.read_text())
    fake_llm.responses = [json.dumps([
        {
            "msg_indices": [0, 1],
            "drawer_topic": "北京消费问答",
            "summary": "用户问在北京花了多少，算出 69 万元",
            "keywords": ["北京", "消费"],
            "proposed_room_label": "消费",
        },
        {
            "msg_indices": [2, 3],
            "drawer_topic": "下周会议安排",
            "summary": "安排三个会议：周二、四、五",
            "keywords": ["会议", "下周"],
            "proposed_room_label": "工作",
        },
    ])]
    chunker = Chunker(llm=fake_llm)
    chunks = await chunker.chunk_and_summarise(session)
    assert len(chunks) == 2
    assert chunks[0].proposed_room_label == "消费"
    assert chunks[0].summary.startswith("用户问")
    assert chunks[1].proposed_room_label == "工作"


async def test_chunker_empty(fake_llm):
    from mybot.palace.chunker import Chunker
    chunker = Chunker(llm=fake_llm)
    assert await chunker.chunk_and_summarise([]) == []


async def test_chunker_handles_llm_failure(fake_llm):
    from mybot.palace.chunker import Chunker
    # no responses queued → FakeLLM raises → chunker returns []
    chunker = Chunker(llm=fake_llm)
    out = await chunker.chunk_and_summarise(
        [{"role": "user", "content": "hi"}]
    )
    assert out == []


async def test_chunker_tolerates_fenced_json(fake_llm):
    from mybot.palace.chunker import Chunker
    fake_llm.responses = ["```json\n" + json.dumps([{
        "msg_indices": [0],
        "drawer_topic": "x",
        "summary": "y",
        "keywords": [],
        "proposed_room_label": "消费",
    }]) + "\n```"]
    chunker = Chunker(llm=fake_llm)
    chunks = await chunker.chunk_and_summarise(
        [{"role": "user", "content": "hi"}]
    )
    assert len(chunks) == 1
