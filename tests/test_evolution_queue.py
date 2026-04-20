"""Tests for EvolutionQueue."""

import json
import pytest
from mybot.evolution.queue import EvolutionQueue


@pytest.fixture
async def queue(tmp_path):
    q = EvolutionQueue(db_path=tmp_path / "evo.db")
    await q.initialize()
    yield q
    await q.close()


async def test_insert_and_get(queue):
    eid = await queue.insert(
        type="skill",
        source="skillforge",
        payload={"name": "test_skill", "trigger": "test"},
    )
    assert eid  # non-empty string
    item = await queue.get(eid)
    assert item is not None
    assert item["type"] == "skill"
    assert item["source"] == "skillforge"
    assert item["status"] == "proposed"
    assert json.loads(item["payload"])["name"] == "test_skill"


async def test_list_by_status(queue):
    await queue.insert(type="skill", source="skillforge", payload={"a": 1})
    await queue.insert(type="skill", source="skillforge", payload={"b": 2})
    items = await queue.list_by_status("proposed")
    assert len(items) == 2


async def test_list_by_type_and_status(queue):
    await queue.insert(type="skill", source="skillforge", payload={"a": 1})
    await queue.insert(type="prompt_tweak", source="mirror", payload={"b": 2})
    skills = await queue.list_by_status("proposed", type="skill")
    assert len(skills) == 1
    assert skills[0]["type"] == "skill"


async def test_update_status(queue):
    eid = await queue.insert(type="skill", source="skillforge", payload={})
    await queue.update_status(eid, "approved")
    item = await queue.get(eid)
    assert item["status"] == "approved"
    assert item["reviewed_at"] is not None


async def test_update_status_applied(queue):
    eid = await queue.insert(type="skill", source="skillforge", payload={})
    await queue.update_status(eid, "applied")
    item = await queue.get(eid)
    assert item["status"] == "applied"
    assert item["applied_at"] is not None


async def test_expire_old_proposals(queue):
    eid = await queue.insert(type="skill", source="skillforge", payload={}, expires_in_days=-1)
    expired_count = await queue.expire_stale()
    assert expired_count == 1
    item = await queue.get(eid)
    assert item["status"] == "expired"


async def test_expire_skips_fresh(queue):
    await queue.insert(type="skill", source="skillforge", payload={}, expires_in_days=30)
    expired_count = await queue.expire_stale()
    assert expired_count == 0


async def test_delete_old_chat_events(queue):
    eid = await queue.insert(type="chat_event", source="agent", payload={}, expires_in_days=-1)
    deleted = await queue.cleanup_chat_events(max_age_days=0)
    assert deleted == 1
    item = await queue.get(eid)
    assert item is None


async def test_insert_chat_event(queue):
    eid = await queue.insert_chat_event(
        session_id="tg-12345",
        tool_calls=[
            {"name": "palace", "success": True, "latency_ms": 230},
            {"name": "web_search", "success": False, "latency_ms": 5100},
        ],
        memory_hit=True,
        negative_signal=False,
        turn_count=4,
    )
    item = await queue.get(eid)
    assert item["type"] == "chat_event"
    assert item["source"] == "agent"
    payload = json.loads(item["payload"])
    assert payload["session_id"] == "tg-12345"
    assert len(payload["tool_calls"]) == 2
    assert payload["memory_hit"] is True
    assert payload["turn_count"] == 4
