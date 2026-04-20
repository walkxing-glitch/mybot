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
