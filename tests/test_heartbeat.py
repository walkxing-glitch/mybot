"""Tests for HeartbeatLoop."""

import asyncio
import pytest
from unittest.mock import AsyncMock
from mybot.evolution.heartbeat import HeartbeatLoop
from mybot.config import HeartbeatConfig


@pytest.fixture
def config():
    return HeartbeatConfig(enabled=True, interval_seconds=0.1)


async def test_heartbeat_ticks(config):
    on_tick = AsyncMock()
    loop = HeartbeatLoop(config=config, on_tick=on_tick)
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.35)
    loop.stop()
    await task
    assert on_tick.call_count >= 2


async def test_heartbeat_defers_when_busy(config):
    on_tick = AsyncMock()
    loop = HeartbeatLoop(config=config, on_tick=on_tick)
    loop.set_busy(True)
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.25)
    assert on_tick.call_count == 0
    loop.set_busy(False)
    await asyncio.sleep(0.15)
    loop.stop()
    await task
    assert on_tick.call_count >= 1


async def test_heartbeat_disabled():
    config = HeartbeatConfig(enabled=False)
    on_tick = AsyncMock()
    loop = HeartbeatLoop(config=config, on_tick=on_tick)
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.2)
    loop.stop()
    await task
    assert on_tick.call_count == 0


async def test_heartbeat_tick_runs_expire_and_cleanup(tmp_path):
    """Integration: heartbeat tick should expire stale and cleanup old events."""
    from mybot.evolution.queue import EvolutionQueue

    queue = EvolutionQueue(db_path=tmp_path / "evo.db")
    await queue.initialize()

    # Insert a stale proposal (already expired)
    await queue.insert(type="skill", source="test", payload={}, expires_in_days=-1)
    # Insert an old chat_event
    await queue.insert(type="chat_event", source="agent", payload={}, expires_in_days=-1)

    async def on_tick():
        await queue.expire_stale()
        await queue.cleanup_chat_events(max_age_days=0)

    config = HeartbeatConfig(enabled=True, interval_seconds=0.1)
    loop = HeartbeatLoop(config=config, on_tick=on_tick)
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.25)
    loop.stop()
    await task

    # Stale proposal should be expired
    proposals = await queue.list_by_status("proposed")
    assert len(proposals) == 0
    expired = await queue.list_by_status("expired")
    assert len(expired) == 1

    # Old chat_event should be deleted
    events = await queue.list_by_status("proposed", type="chat_event")
    assert len(events) == 0

    await queue.close()
