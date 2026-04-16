"""Anti-rust E2E: failure narratives must never reach atrium or later prompts.

This is the regression test for the 2026-04-16 bug where an 'ontology 服务
不可用' tool-failure message got written as a long-term 'fact' and poisoned
subsequent conversations.
"""
from __future__ import annotations

import json
from pathlib import Path

from mybot.palace import MemoryPalace
from mybot.palace.config import PalaceConfig


FIXTURE = Path(__file__).parent / "fixtures" / "tool_failure_session.json"


class _Reranker:
    def rerank(self, q, docs):
        return [1.0] * len(docs)


async def test_failure_session_does_not_pollute_atrium(
    tmp_path, fake_llm, fake_embedder,
):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    session = json.loads(FIXTURE.read_text())
    fake_llm.responses = [json.dumps([{
        "msg_indices": [0, 1, 2],
        "drawer_topic": "查询失败",
        "summary": "用户问北京消费，工具报错，未能返回结果",
        "keywords": ["北京", "消费", "失败"],
        "proposed_room_label": "消费",
    }])]
    palace = MemoryPalace(
        cfg=cfg, llm=fake_llm,
        embedder=fake_embedder, reranker=_Reranker(),
    )
    await palace.initialize()
    await palace.archive_session(
        "fail-morning", session,
        now_date="2026-04-16", now_year=2026,
    )

    entries = await palace.store.list_atrium_entries()
    assert len(entries) == 0, f"atrium should be empty, got {entries}"

    ctx = await palace.assemble_context(
        "我在北京花了多少钱",
        now_year=2026, now_date="2026-04-16",
    )
    assert "🏛️" not in ctx
    assert "[规则]" not in ctx
    assert "[偏好]" not in ctx
    assert "[事实]" not in ctx
    await palace.close()


async def test_blacklist_blocks_explicit_attempt(
    tmp_path, fake_llm, fake_embedder,
):
    """Even if user explicitly says '记住 XX 不可用', it must NOT enter atrium."""
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    session = [
        {"role": "user", "content": "记住：端口 8003 不可用"},
    ]
    fake_llm.responses = [
        json.dumps([]),
        json.dumps([{
            "entry_type": "rule",
            "content": "端口 8003 不可用",
        }]),
    ]
    palace = MemoryPalace(
        cfg=cfg, llm=fake_llm,
        embedder=fake_embedder, reranker=_Reranker(),
    )
    await palace.initialize()
    await palace.archive_session(
        "s", session, now_date="2026-04-16", now_year=2026,
    )
    entries = await palace.store.list_atrium_entries()
    assert len(entries) == 0
    await palace.close()


async def test_blacklist_phrases_all_eight(
    tmp_path, fake_llm, fake_embedder,
):
    """All eight default blacklist phrases must be rejected when user tries
    to explicitly memorialise them."""
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    bad_contents = [
        "端口 8003 不可用",
        "查询未能找到记录",
        "服务中断后无法恢复",
        "API 请求超时了",
        "ontology 工具报错",
        "外部系统无法访问",
        "数据写入操作失败",
        "数据库连接失败",
    ]
    palace = MemoryPalace(
        cfg=cfg, llm=fake_llm,
        embedder=fake_embedder, reranker=_Reranker(),
    )
    await palace.initialize()
    for c in bad_contents:
        fake_llm.responses = [
            json.dumps([]),
            json.dumps([{"entry_type": "rule", "content": c}]),
        ]
        await palace.archive_session(
            "s", [{"role": "user", "content": f"记住：{c}"}],
            now_date="2026-04-16", now_year=2026,
        )
    entries = await palace.store.list_atrium_entries()
    assert len(entries) == 0, (
        f"blacklist leaked: {[e['content'] for e in entries]}"
    )
    await palace.close()


async def test_legitimate_explicit_rule_passes(
    tmp_path, fake_llm, fake_embedder,
):
    """Control: a clean explicit rule should still be saved."""
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    fake_llm.responses = [
        json.dumps([]),
        json.dumps([{
            "entry_type": "rule",
            "content": "以后别在测试里加 mock 数据库",
        }]),
    ]
    palace = MemoryPalace(
        cfg=cfg, llm=fake_llm,
        embedder=fake_embedder, reranker=_Reranker(),
    )
    await palace.initialize()
    await palace.archive_session(
        "s", [{"role": "user", "content": "记住：以后别在测试里加 mock 数据库"}],
        now_date="2026-04-16", now_year=2026,
    )
    entries = await palace.store.list_atrium_entries(status="active")
    assert len(entries) == 1
    assert "mock" in entries[0]["content"]
    await palace.close()
