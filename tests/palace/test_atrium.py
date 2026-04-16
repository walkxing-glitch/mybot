"""AtriumManager.assemble_block tests."""
from __future__ import annotations

import uuid

from mybot.palace.atrium import AtriumManager
from mybot.palace.config import PalaceConfig


async def test_atrium_assemble(store, fake_embedder):
    cfg = PalaceConfig()
    for etype, c in [
        ("rule", "别启动 myontology/backend"),
        ("preference", "喜欢简洁直接的回答"),
        ("fact", "真名邢智强"),
    ]:
        emb = fake_embedder.encode(c)[0] if etype == "fact" else None
        await store.insert_atrium_entry(
            id=str(uuid.uuid4()), entry_type=etype, content=c,
            source_type="explicit", status="active",
            embedding=emb,
        )
    mgr = AtriumManager(cfg=cfg, store=store, embedder=fake_embedder)
    block = await mgr.assemble_block(query="身份 / 规则 / 偏好", now_year=2026)
    assert "[规则]" in block
    assert "myontology/backend" in block
    assert "[偏好]" in block
    assert "[事实]" in block
    assert "邢智强" in block


async def test_atrium_assemble_empty_when_no_active(store, fake_embedder):
    mgr = AtriumManager(cfg=PalaceConfig(), store=store, embedder=fake_embedder)
    block = await mgr.assemble_block(query="anything", now_year=2026)
    assert block == ""


async def test_atrium_assemble_ignores_rejected(store, fake_embedder):
    await store.insert_atrium_entry(
        id=str(uuid.uuid4()), entry_type="rule", content="旧规则",
        source_type="explicit", status="rejected",
    )
    mgr = AtriumManager(cfg=PalaceConfig(), store=store, embedder=fake_embedder)
    block = await mgr.assemble_block(query="x", now_year=2026)
    assert "旧规则" not in block
