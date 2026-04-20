"""Tests for PalaceClient and PalaceHttpTool (mock HTTP, no real server)."""

from __future__ import annotations

import json

import httpx
import pytest

from mybot.tools.palace_client import PalaceClient, PalaceHttpTool


def _mock_transport(handler):
    """Create an httpx.MockTransport from an async handler."""
    return httpx.MockTransport(handler)


def _make_client(handler) -> PalaceClient:
    client = PalaceClient.__new__(PalaceClient)
    client.base_url = "http://test"
    client._client = httpx.AsyncClient(
        transport=_mock_transport(handler), base_url="http://test"
    )
    return client


# --------------- PalaceClient tests ---------------


async def test_get_context_for_prompt():
    async def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/session/context"
        body = json.loads(req.content)
        return httpx.Response(200, json={"context": f"ctx for {body['query']}"})

    client = _make_client(handler)
    result = await client.get_context_for_prompt("hello")
    assert result == "ctx for hello"
    await client.close()


async def test_get_context_for_prompt_failure():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _make_client(handler)
    result = await client.get_context_for_prompt("hello")
    assert result == ""
    await client.close()


async def test_end_session():
    async def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/session/archive"
        body = json.loads(req.content)
        return httpx.Response(200, json={
            "session_id": body["session_id"],
            "north_ids": ["N-1"],
            "south_ids": [],
            "atrium_ids": [],
            "merge_count": 0,
        })

    client = _make_client(handler)
    result = await client.end_session("sess-1", [{"role": "user", "content": "hi"}])
    assert result["session_id"] == "sess-1"
    assert result["north_ids"] == ["N-1"]
    await client.close()


async def test_end_session_failure():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(502)

    client = _make_client(handler)
    result = await client.end_session("sess-1", [])
    assert result["session_id"] == "sess-1"
    assert result["north_ids"] == []
    await client.close()


async def test_get_stats():
    async def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/stats"
        return httpx.Response(200, json={"north": 10, "south": 5})

    client = _make_client(handler)
    result = await client.get_stats()
    assert result == {"north": 10, "south": 5}
    await client.close()


async def test_get_day_room_map():
    async def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/drawers/2026-04-18"
        return httpx.Response(200, json={"1": ["N-2026-001-01-01"]})

    client = _make_client(handler)
    result = await client.get_day_room_map("2026-04-18")
    assert "1" in result
    await client.close()


async def test_get_atrium_entries():
    async def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/atrium"
        assert req.url.params["status"] == "active"
        return httpx.Response(200, json=[{"id": "a1", "type": "rule"}])

    client = _make_client(handler)
    result = await client.get_atrium_entries()
    assert len(result) == 1
    assert result[0]["id"] == "a1"
    await client.close()


async def test_get_atrium_entries_with_type():
    async def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.params["entry_type"] == "fact"
        return httpx.Response(200, json=[])

    client = _make_client(handler)
    result = await client.get_atrium_entries(entry_type="fact")
    assert result == []
    await client.close()


async def test_get_atrium_entry():
    async def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/atrium/a1"
        return httpx.Response(200, json={"id": "a1", "content": "test"})

    client = _make_client(handler)
    result = await client.get_atrium_entry("a1")
    assert result["id"] == "a1"
    await client.close()


async def test_get_atrium_entry_not_found():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _make_client(handler)
    result = await client.get_atrium_entry("missing")
    assert result is None
    await client.close()


async def test_get_drawer_raw():
    async def handler(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/drawers/N-2026-001-01-01/raw"
        return httpx.Response(200, json={"messages": [{"role": "user", "content": "hi"}]})

    client = _make_client(handler)
    result = await client.get_drawer_raw("N-2026-001-01-01")
    assert "messages" in result
    await client.close()


async def test_get_drawer_raw_not_found():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _make_client(handler)
    result = await client.get_drawer_raw("missing")
    assert result is None
    await client.close()


# --------------- PalaceHttpTool tests ---------------


def _make_tool(handler) -> PalaceHttpTool:
    return PalaceHttpTool(_make_client(handler))


async def test_tool_stats():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"north": 3})

    tool = _make_tool(handler)
    result = await tool.execute(operation="stats")
    assert result.success
    assert "north" in result.output
    await tool.client.close()


async def test_tool_list_day_drawers():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"1": ["N-2026-001-01-01"]})

    tool = _make_tool(handler)
    result = await tool.execute(operation="list_day_drawers", date="2026-04-18")
    assert result.success
    await tool.client.close()


async def test_tool_list_day_drawers_missing_date():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    tool = _make_tool(handler)
    result = await tool.execute(operation="list_day_drawers")
    assert not result.success
    assert "date" in result.error
    await tool.client.close()


async def test_tool_get_raw_conversation():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"messages": []})

    tool = _make_tool(handler)
    result = await tool.execute(operation="get_raw_conversation", drawer_id="N-2026-001-01-01")
    assert result.success
    await tool.client.close()


async def test_tool_get_raw_missing_id():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    tool = _make_tool(handler)
    result = await tool.execute(operation="get_raw_conversation")
    assert not result.success
    assert "drawer_id" in result.error
    await tool.client.close()


async def test_tool_list_atrium():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[{"id": "a1"}])

    tool = _make_tool(handler)
    result = await tool.execute(operation="list_atrium")
    assert result.success
    await tool.client.close()


async def test_tool_show_atrium_entry():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"id": "a1", "content": "x"})

    tool = _make_tool(handler)
    result = await tool.execute(operation="show_atrium_entry", entry_id="a1")
    assert result.success
    await tool.client.close()


async def test_tool_show_atrium_missing_id():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    tool = _make_tool(handler)
    result = await tool.execute(operation="show_atrium_entry")
    assert not result.success
    assert "entry_id" in result.error
    await tool.client.close()


async def test_tool_unknown_operation():
    async def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={})

    tool = _make_tool(handler)
    result = await tool.execute(operation="nonexistent")
    assert not result.success
    assert "unknown" in result.error
    await tool.client.close()
