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
