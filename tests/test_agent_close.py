"""Tests for Agent.close() lifecycle management and background task tracking."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from mybot.agent import Agent
from mybot.tools.base import BaseTool, ToolResult


class FakeClosableTool(BaseTool):
    name = "fake_closable"
    description = "tool with a close() method"
    parameters = {"type": "object", "properties": {}, "required": []}

    def __init__(self) -> None:
        self.close_called = False

    async def execute(self, **params: Any) -> ToolResult:
        return ToolResult(success=True, output="ok")

    async def close(self) -> None:
        self.close_called = True


class FakeMemoryEngine:
    def __init__(self) -> None:
        self.close_called = False
        self.end_session_calls: list[list[dict[str, Any]]] = []

    async def get_context_for_prompt(self, query: str) -> str:
        return ""

    async def end_session(
        self, *, session_id: str, conversation_messages: list[dict[str, Any]]
    ) -> None:
        self.end_session_calls.append(list(conversation_messages))

    async def close(self) -> None:
        self.close_called = True


async def test_close_drains_background_tasks(tmp_path):
    """Agent.close() should await all tracked background tasks."""
    from mybot.evolution.queue import EvolutionQueue

    queue = EvolutionQueue(db_path=tmp_path / "evo.db")
    await queue.initialize()

    memory = FakeMemoryEngine()
    tool = FakeClosableTool()
    agent = Agent(
        config=None,
        memory_engine=memory,
        tools=[tool],
        evolution_queue=queue,
    )

    call_count = 0

    async def mock_completion(messages, tools=None, model=None, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "choices": [{"message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "fake_closable", "arguments": "{}"},
                    }],
                }}],
            }
        return {
            "choices": [{"message": {"role": "assistant", "content": "reply"}}],
        }

    with patch("mybot.agent.completion", side_effect=mock_completion):
        await agent.chat("sess-1", "hello")

    # Tasks should be tracked, not yet awaited
    assert len(agent._background_tasks) >= 1

    await agent.close()

    # After close: all tasks drained, and resources closed
    assert len(agent._background_tasks) == 0
    assert memory.close_called is True
    assert tool.close_called is True
    assert len(memory.end_session_calls) == 1

    # Verify chat_event was persisted by opening a fresh queue on the same DB
    queue2 = EvolutionQueue(db_path=tmp_path / "evo.db")
    await queue2.initialize()
    events = await queue2.list_by_status("proposed", type="chat_event")
    assert len(events) == 1
    await queue2.close()


async def test_close_is_idempotent():
    """Calling close() twice should be safe."""
    agent = Agent(config=None, memory_engine=None, tools=[])
    await agent.close()
    await agent.close()  # second call should be a no-op, not raise
    assert agent._closed is True


async def test_close_skips_resources_without_close_method():
    """Resources without close() method should be silently skipped."""

    class NoCloseEngine:
        async def get_context_for_prompt(self, q: str) -> str:
            return ""

    agent = Agent(config=None, memory_engine=NoCloseEngine(), tools=[])
    await agent.close()  # should not raise


async def test_close_after_close_prevents_new_tasks(tmp_path):
    """After close(), chat() should not spawn new background tasks."""
    from mybot.evolution.queue import EvolutionQueue

    queue = EvolutionQueue(db_path=tmp_path / "evo.db")
    await queue.initialize()

    agent = Agent(config=None, memory_engine=None, tools=[], evolution_queue=queue)
    await agent.close()

    async def mock_completion(messages, tools=None, model=None, **kw):
        return {"choices": [{"message": {"role": "assistant", "content": "ok"}}]}

    with patch("mybot.agent.completion", side_effect=mock_completion):
        await agent.chat("sess-1", "hi")

    # No new chat_event task should have been spawned (agent was closed)
    assert len(agent._background_tasks) == 0


async def test_memory_snapshot_taken_before_trim():
    """The memory snapshot passed to _post_process_memory must be taken before session.trim()."""
    memory = FakeMemoryEngine()
    agent = Agent(config=None, memory_engine=memory, tools=[])

    async def mock_completion(messages, tools=None, model=None, **kw):
        return {"choices": [{"message": {"role": "assistant", "content": "reply"}}]}

    # Pre-fill session with many messages to force trim()
    session = agent._get_or_create_session("sess-1")
    session.messages.extend(
        [{"role": "user", "content": f"msg{i}"} for i in range(50)]
    )

    with patch("mybot.agent.completion", side_effect=mock_completion):
        await agent.chat("sess-1", "new message")

    await agent.close()

    # The snapshot should contain the full 50+ messages, not the trimmed view
    assert len(memory.end_session_calls) == 1
    assert len(memory.end_session_calls[0]) > 40  # trim keeps 40, snapshot kept more
