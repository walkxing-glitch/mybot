"""Tests for PalaceStore CRUD paths."""
from __future__ import annotations

import apsw
import pytest


async def test_store_initialize_idempotent(store):
    # fixture already initialized; second call must not fail
    await store.initialize()
    await store.insert_north_drawer(
        year=2026, floor=1, room=1, drawer=1,
        date="2026-01-01", raw_messages=[],
    )
    rec = await store.get_north_drawer("N-2026-001-01-01")
    assert rec is not None


async def test_north_insert_and_get(store):
    drawer_id = await store.insert_north_drawer(
        year=2026, floor=107, room=5, drawer=7,
        date="2026-04-16",
        raw_messages=[{"role": "user", "content": "hi"}],
    )
    assert drawer_id == "N-2026-107-05-07"
    row = await store.get_north_drawer(drawer_id)
    assert row["date"] == "2026-04-16"
    assert row["raw_messages"][0]["content"] == "hi"
    assert row["message_count"] == 1


async def test_north_unique_coord(store):
    await store.insert_north_drawer(
        year=2026, floor=1, room=1, drawer=1,
        date="2026-01-01", raw_messages=[],
    )
    with pytest.raises(apsw.ConstraintError):
        await store.insert_north_drawer(
            year=2026, floor=1, room=1, drawer=1,
            date="2026-01-01", raw_messages=[],
        )


async def test_south_insert_and_fts(store, fake_embedder):
    emb = fake_embedder.encode("北京消费讨论")[0]
    drawer_id = await store.insert_south_drawer(
        year=2026, floor=107, room=1, drawer=1,
        date="2026-04-16",
        north_ref_ids=["N-2026-107-01-01"],
        room_type="fixed", room_label="消费",
        drawer_topic="北京消费问答",
        summary="用户问在北京花了多少钱，核算出 69 万元",
        keywords=["北京", "消费", "69 万"],
        embedding=emb,
    )
    assert drawer_id == "S-2026-107-01-01"
    hits = await store.fts_search("北京", limit=5)
    assert any(h["drawer_id"] == drawer_id for h in hits)


async def test_south_get_roundtrip(store, fake_embedder):
    emb = fake_embedder.encode("topic")[0]
    drawer_id = await store.insert_south_drawer(
        year=2026, floor=1, room=2, drawer=3,
        date="2026-04-16",
        north_ref_ids=["N-2026-001-02-03"],
        room_type="fixed", room_label="工作",
        drawer_topic="t", summary="s", keywords=["a", "b"],
        embedding=emb,
    )
    rec = await store.get_south_drawer(drawer_id)
    assert rec["drawer_topic"] == "t"
    assert rec["keywords"] == ["a", "b"]
    assert rec["north_ref_ids"] == ["N-2026-001-02-03"]
    assert rec["merge_count"] == 1


async def test_vec_knn(store, fake_embedder):
    texts = ["北京消费讨论", "上海工作安排", "深圳旅游计划"]
    for i, t in enumerate(texts):
        emb = fake_embedder.encode(t)[0]
        await store.insert_south_drawer(
            year=2026, floor=100 + i, room=1, drawer=1,
            date=f"2026-04-{10 + i:02d}",
            north_ref_ids=[f"N-2026-{100 + i:03d}-01-01"],
            room_type="fixed", room_label="消费",
            drawer_topic=t, summary=t, keywords=[],
            embedding=emb,
        )
    q = fake_embedder.encode("北京消费讨论")[0]
    hits = await store.vec_knn(q, limit=3)
    assert hits[0]["drawer_id"] == "S-2026-100-01-01"


async def test_merge_south_drawer(store, fake_embedder):
    emb = fake_embedder.encode("seed")[0]
    drawer_id = await store.insert_south_drawer(
        year=2026, floor=1, room=1, drawer=1,
        date="2026-04-16",
        north_ref_ids=["N-2026-001-01-01"],
        room_type="fixed", room_label="消费",
        drawer_topic="t", summary="s1", keywords=[],
        embedding=emb,
    )
    new_emb = fake_embedder.encode("merged")[0]
    await store.merge_south_drawer(
        target_id=drawer_id,
        new_north_id="N-2026-001-01-02",
        new_summary="s1+s2",
        new_embedding=new_emb,
    )
    rec = await store.get_south_drawer(drawer_id)
    assert rec["summary"] == "s1+s2"
    assert rec["merge_count"] == 2
    assert len(rec["north_ref_ids"]) == 2


async def test_day_room_map(store):
    await store.upsert_day_room(
        date="2026-04-16", room=1, room_type="fixed",
        room_label="消费", drawer_count=3,
    )
    rooms = await store.get_day_room_map("2026-04-16")
    assert rooms[1]["room_label"] == "消费"
    assert rooms[1]["drawer_count"] == 3
    await store.upsert_day_room(
        date="2026-04-16", room=1, room_type="fixed",
        room_label="消费", drawer_count=4,
    )
    rooms = await store.get_day_room_map("2026-04-16")
    assert rooms[1]["drawer_count"] == 4


async def test_fts_empty_query_returns_empty(store):
    assert await store.fts_search("") == []
    assert await store.fts_search("   ") == []
