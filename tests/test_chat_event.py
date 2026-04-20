# tests/test_chat_event.py
"""Tests for chat_event emission from Agent."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from mybot.agent import Agent
from mybot.tools.base import BaseTool, ToolResult


class FakeTool(BaseTool):
    name = "fake"
    description = "test tool"
    parameters = {"type": "object", "properties": {"x": {"type": "string"}}, "required": []}

    async def execute(self, **params):
        return ToolResult(success=True, output="ok")


async def test_tool_log_collected():
    """Agent._run_tool_loop should return (text, tool_log)."""
    agent = Agent(config=None, memory_engine=None, tools=[FakeTool()])

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
                        "function": {"name": "fake", "arguments": "{}"},
                    }],
                }}],
            }
        return {
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        }

    with patch("mybot.agent.completion", side_effect=mock_completion):
        session = agent._get_or_create_session("test")
        session.append({"role": "user", "content": "hi"})
        text, tool_log = await agent._run_tool_loop(session, "system prompt")

    assert text == "done"
    assert len(tool_log) == 1
    assert tool_log[0]["name"] == "fake"
    assert tool_log[0]["success"] is True
    assert "latency_ms" in tool_log[0]
