"""Tests for OntologyTool and NeuralTwinTool persistent httpx clients."""

from __future__ import annotations

import httpx

from mybot.tools.neural_twin import NeuralTwinTool
from mybot.tools.ontology import OntologyTool


async def test_ontology_tool_close_releases_client():
    tool = OntologyTool()
    assert tool._client is not None
    assert not tool._client.is_closed
    await tool.close()
    assert tool._client.is_closed


async def test_ontology_tool_reuses_client_across_calls():
    call_count = 0

    async def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"stats": {"ok": True}})

    tool = OntologyTool.__new__(OntologyTool)
    tool.base_url = "http://test"
    tool.ontology_id = "abc"
    tool.timeout = 30.0
    tool._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )

    r1 = await tool.execute(operation="get_stats")
    r2 = await tool.execute(operation="get_stats")
    assert r1.success is True
    assert r2.success is True
    assert call_count == 2  # two HTTP calls made, one client reused
    await tool.close()


async def test_neural_twin_tool_close_releases_client():
    tool = NeuralTwinTool()
    assert tool._client is not None
    assert not tool._client.is_closed
    await tool.close()
    assert tool._client.is_closed


async def test_neural_twin_tool_reuses_client_across_calls():
    call_count = 0

    async def handler(req: httpx.Request) -> httpx.Response:
        nonlocal call_count
        call_count += 1
        return httpx.Response(200, json={"forecast": "ok"})

    tool = NeuralTwinTool.__new__(NeuralTwinTool)
    tool.base_url = "http://test"
    tool.timeout = 30.0
    tool._client = httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="http://test",
    )

    r1 = await tool.execute(operation="get_day_forecast")
    r2 = await tool.execute(operation="get_day_forecast")
    assert r1.success is True
    assert r2.success is True
    assert call_count == 2
    await tool.close()
