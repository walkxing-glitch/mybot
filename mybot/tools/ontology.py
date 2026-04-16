"""Ontology tool — query the myontology API for entities, relationships, transactions."""

from __future__ import annotations

import json
from typing import Any

import httpx

from mybot.tools.base import BaseTool, ToolResult

DEFAULT_BASE_URL = "http://localhost:8003"
DEFAULT_TIMEOUT = 15.0

_ALLOWED_OPS = (
    "search_entities",
    "get_entity",
    "get_relationships",
    "query_transactions",
    "get_spending_summary",
)


class OntologyTool(BaseTool):
    """HTTP client for the myontology knowledge-graph API."""

    name = "ontology"
    description = (
        "Query the personal ontology knowledge graph (people, merchants, "
        "transactions, relationships, spending patterns). Supports: "
        "search_entities, get_entity, get_relationships, query_transactions, "
        "get_spending_summary."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(_ALLOWED_OPS),
                "description": "Which ontology operation to run.",
            },
            "query": {
                "type": "string",
                "description": "Free-text query (for search_entities, query_transactions).",
            },
            "entity_id": {
                "type": "string",
                "description": "Entity ID (for get_entity, get_relationships).",
            },
            "entity_type": {
                "type": "string",
                "description": "Entity type filter (for search_entities), e.g. 'person', 'merchant'.",
            },
            "period": {
                "type": "string",
                "description": "Time period for get_spending_summary, e.g. '7d', '30d', '2026-04'.",
            },
        },
        "required": ["operation"],
    }

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = float(timeout)

    # ---------------------------------------------------------------- helpers

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> ToolResult:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params or {})
        except httpx.ConnectError as exc:
            return ToolResult(
                success=False,
                output="",
                error=f"Cannot reach ontology API at {self.base_url}: {exc}. "
                      "Is myontology running?",
            )
        except httpx.TimeoutException:
            return ToolResult(
                success=False,
                output="",
                error=f"Ontology API timed out after {self.timeout:.0f}s.",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"HTTP error: {exc}")

        if resp.status_code >= 400:
            return ToolResult(
                success=False,
                output="",
                error=f"Ontology API returned HTTP {resp.status_code}: {resp.text[:500]}",
            )

        try:
            data = resp.json()
        except ValueError:
            data = resp.text
        return ToolResult(
            success=True,
            output=json.dumps(data, ensure_ascii=False, indent=2)
            if not isinstance(data, str) else data,
        )

    # -------------------------------------------------------------- operations

    async def execute(self, **params) -> ToolResult:
        op = params.get("operation")
        if op not in _ALLOWED_OPS:
            return ToolResult(
                success=False,
                output="",
                error=f"Unknown operation {op!r}. Allowed: {', '.join(_ALLOWED_OPS)}.",
            )

        try:
            if op == "search_entities":
                query = params.get("query")
                if not query:
                    return ToolResult(success=False, output="", error="'query' is required for search_entities.")
                qp: dict[str, Any] = {"q": query}
                if params.get("entity_type"):
                    qp["type"] = params["entity_type"]
                return await self._get("/api/v1/objects/search", qp)

            if op == "get_entity":
                eid = params.get("entity_id")
                if not eid:
                    return ToolResult(success=False, output="", error="'entity_id' is required for get_entity.")
                return await self._get(f"/api/v1/objects/{eid}")

            if op == "get_relationships":
                eid = params.get("entity_id")
                if not eid:
                    return ToolResult(success=False, output="", error="'entity_id' is required for get_relationships.")
                return await self._get(f"/api/v1/objects/{eid}/links")

            if op == "query_transactions":
                query = params.get("query")
                qp2: dict[str, Any] = {}
                if query:
                    qp2["q"] = query
                return await self._get("/api/v1/transactions", qp2)

            if op == "get_spending_summary":
                period = params.get("period") or "30d"
                return await self._get("/api/v1/value-graph/summary", {"period": period})

        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"Unhandled error: {exc}")

        return ToolResult(success=False, output="", error="Unreachable branch.")


tools = [OntologyTool()]
