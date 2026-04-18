"""HTTP client for soho-twin-towers, compatible with memory_engine interface."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

from mybot.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class PalaceClient:
    """HTTP client that wraps soho-twin-towers REST API.

    Implements the same interface as MemoryPalace (get_context_for_prompt,
    end_session, get_stats) so it can be injected as memory_engine.
    """

    def __init__(self, base_url: str = "http://localhost:8004", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self._client = httpx.AsyncClient(base_url=self.base_url, timeout=timeout)

    async def close(self) -> None:
        await self._client.aclose()

    async def get_context_for_prompt(self, query: str) -> str:
        try:
            resp = await self._client.post("/session/context", json={"query": query})
            resp.raise_for_status()
            return resp.json().get("context", "")
        except Exception as exc:
            logger.warning("Palace context failed: %s", exc)
            return ""

    async def end_session(self, session_id: str, conversation_messages: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            resp = await self._client.post(
                "/session/archive",
                json={"session_id": session_id, "messages": conversation_messages},
            )
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Palace archive failed: %s", exc)
            return {"session_id": session_id, "north_ids": [], "south_ids": [], "atrium_ids": [], "merge_count": 0}

    async def get_stats(self) -> dict[str, int]:
        try:
            resp = await self._client.get("/stats")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Palace stats failed: %s", exc)
            return {}

    async def get_day_room_map(self, date: str) -> dict:
        try:
            resp = await self._client.get(f"/drawers/{date}")
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Palace drawers failed: %s", exc)
            return {}

    async def get_atrium_entries(self, *, status: str = "active", entry_type: str | None = None) -> list[dict]:
        try:
            params: dict[str, str] = {"status": status}
            if entry_type:
                params["entry_type"] = entry_type
            resp = await self._client.get("/atrium", params=params)
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Palace atrium list failed: %s", exc)
            return []

    async def get_atrium_entry(self, entry_id: str) -> dict | None:
        try:
            resp = await self._client.get(f"/atrium/{entry_id}")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Palace atrium get failed: %s", exc)
            return None

    async def get_drawer_raw(self, drawer_id: str) -> dict | None:
        try:
            resp = await self._client.get(f"/drawers/{drawer_id}/raw")
            if resp.status_code == 404:
                return None
            resp.raise_for_status()
            return resp.json()
        except Exception as exc:
            logger.warning("Palace drawer raw failed: %s", exc)
            return None


class PalaceHttpTool(BaseTool):
    """Agent tool that calls soho-twin-towers via HTTP."""

    name = "palace"
    description = (
        "查丽泽SOHO双塔DNA记忆系统。可按坐标取原文 (get_raw_conversation)、"
        "列某天的抽屉 (list_day_drawers)、看中庭条目 (list_atrium / "
        "show_atrium_entry)、看统计 (stats)。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "get_raw_conversation", "list_day_drawers",
                    "list_atrium", "show_atrium_entry", "stats",
                ],
            },
            "drawer_id": {
                "type": "string",
                "description": "N-YYYY-FFF-RR-DD 北塔 or S-YYYY-FFF-RR-DD 南塔坐标",
            },
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "entry_id": {"type": "string"},
            "entry_type": {
                "type": "string",
                "enum": ["rule", "preference", "fact"],
            },
        },
        "required": ["operation"],
    }

    def __init__(self, palace_client: PalaceClient):
        self.client = palace_client

    async def execute(self, **params: Any) -> ToolResult:
        op = params.get("operation")
        try:
            if op == "get_raw_conversation":
                drawer_id = params.get("drawer_id")
                if not drawer_id:
                    return ToolResult(success=False, output="", error="get_raw_conversation 需要 drawer_id")
                result = await self.client.get_drawer_raw(drawer_id)
                if result is None:
                    return ToolResult(success=False, output="", error=f"no drawer {drawer_id}")
                return ToolResult(success=True, output=json.dumps(result, ensure_ascii=False, default=str))

            if op == "list_day_drawers":
                date = params.get("date")
                if not date:
                    return ToolResult(success=False, output="", error="list_day_drawers 需要 date 参数")
                result = await self.client.get_day_room_map(date)
                return ToolResult(success=True, output=json.dumps(result, ensure_ascii=False))

            if op == "list_atrium":
                entries = await self.client.get_atrium_entries(
                    status="active", entry_type=params.get("entry_type"),
                )
                return ToolResult(success=True, output=json.dumps(entries, ensure_ascii=False, default=str))

            if op == "show_atrium_entry":
                eid = params.get("entry_id")
                if not eid:
                    return ToolResult(success=False, output="", error="show_atrium_entry 需要 entry_id")
                entry = await self.client.get_atrium_entry(eid)
                if entry is None:
                    return ToolResult(success=False, output="", error=f"no entry {eid}")
                return ToolResult(success=True, output=json.dumps(entry, ensure_ascii=False, default=str))

            if op == "stats":
                s = await self.client.get_stats()
                return ToolResult(success=True, output=json.dumps(s, ensure_ascii=False))

            return ToolResult(success=False, output="", error=f"unknown operation {op!r}")
        except Exception as exc:
            return ToolResult(success=False, output="", error=str(exc))
