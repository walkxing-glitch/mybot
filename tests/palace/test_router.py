"""Router tests: fixed / dynamic / misc + overflow merge."""
from __future__ import annotations

from mybot.palace.config import PalaceConfig
from mybot.palace.router import RoomSlot, Router


async def test_fixed_room_mapping(store):
    router = Router(PalaceConfig(), store)
    slot = await router.assign_room(date="2026-04-16", proposed_label="消费")
    assert slot.room == 1
    assert slot.room_type == "fixed"
    assert slot.room_label == "消费"


async def test_dynamic_room_open_then_reuse(store):
    router = Router(PalaceConfig(), store)
    s1 = await router.assign_room(
        date="2026-04-16", proposed_label="书法练习",
    )
    assert s1.room_type == "dynamic"
    assert 11 <= s1.room <= 19
    # persist so next call sees it
    await store.upsert_day_room(
        date="2026-04-16", room=s1.room,
        room_type="dynamic", room_label="书法练习", drawer_count=0,
    )
    s2 = await router.assign_room(
        date="2026-04-16", proposed_label="书法练习",
    )
    assert s2.room == s1.room


async def test_misc_overflow_when_dynamic_slots_full(store):
    router = Router(PalaceConfig(), store)
    for i in range(9):
        label = f"动态主题{i}"
        slot = await router.assign_room(date="2026-04-16", proposed_label=label)
        await store.upsert_day_room(
            date="2026-04-16", room=slot.room,
            room_type=slot.room_type, room_label=label, drawer_count=0,
        )
    final = await router.assign_room(
        date="2026-04-16", proposed_label="又一个新主题",
    )
    assert final.room == 20
    assert final.room_type == "misc"


async def test_assign_drawer_allocates_next(store):
    router = Router(PalaceConfig(), store)
    slot = RoomSlot(room=1, room_type="fixed", room_label="消费")
    d1 = await router.assign_drawer(date="2026-04-16", slot=slot)
    assert d1.drawer == 1
    await store.upsert_day_room(
        date="2026-04-16", room=1, room_type="fixed",
        room_label="消费", drawer_count=5,
    )
    d2 = await router.assign_drawer(date="2026-04-16", slot=slot)
    assert d2.drawer == 6


async def test_assign_drawer_overflow_marks_merge(store):
    router = Router(PalaceConfig(), store)
    slot = RoomSlot(room=1, room_type="fixed", room_label="消费")
    await store.upsert_day_room(
        date="2026-04-16", room=1, room_type="fixed",
        room_label="消费", drawer_count=20,
    )
    d = await router.assign_drawer(date="2026-04-16", slot=slot)
    assert d.drawer == -1
    assert d.is_merge_target is True


async def test_merge_into_existing_uses_llm(store, fake_llm, fake_embedder):
    router = Router(PalaceConfig(), store, embedder=fake_embedder, llm=fake_llm)
    # prepopulate room 1 with 2 drawers
    for i in (1, 2):
        emb = fake_embedder.encode(f"消费事件 {i}")[0]
        await store.insert_south_drawer(
            year=2026, floor=107, room=1, drawer=i,
            date="2026-04-16",
            north_ref_ids=[f"N-2026-107-01-{i:02d}"],
            room_type="fixed", room_label="消费",
            drawer_topic=f"事件{i}", summary=f"消费事件 {i}",
            keywords=["消费"], embedding=emb,
        )
    fake_llm.responses = ["合并后摘要：消费事件 1 与新 chunk 合并"]
    new_emb = fake_embedder.encode("消费事件 1")[0]  # similar to drawer 1
    result = await router.merge_into_existing(
        date="2026-04-16",
        slot=RoomSlot(room=1, room_type="fixed", room_label="消费"),
        new_summary="新 chunk",
        new_north_id="N-2026-107-01-21",
        new_embedding=new_emb,
    )
    target = await store.get_south_drawer(result.target_south_id)
    assert target["merge_count"] == 2
    assert "N-2026-107-01-21" in target["north_ref_ids"]
    assert target["summary"].startswith("合并后摘要")
