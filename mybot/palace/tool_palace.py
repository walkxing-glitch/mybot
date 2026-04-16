"""Palace BaseTool: exposes memory-palace operations to the agent."""
from __future__ import annotations

import json
from typing import Any

from mybot.tools.base import BaseTool, ToolResult


class PalaceTool(BaseTool):
    name = "palace"
    description = (
        "查丽泽园记忆系统。可按坐标取原文 (get_raw_conversation)、"
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

    def __init__(self, palace):
        self.palace = palace

    async def execute(self, **params: Any) -> ToolResult:
        op = params.get("operation")
        try:
            if op == "get_raw_conversation":
                return await self._get_raw_conversation(params)
            if op == "list_day_drawers":
                date = params.get("date")
                if not date:
                    return ToolResult(
                        success=False, output="",
                        error="list_day_drawers 需要 date 参数",
                    )
                rooms = await self.palace.store.get_day_room_map(date)
                return ToolResult(
                    success=True,
                    output=json.dumps(rooms, ensure_ascii=False),
                )
            if op == "list_atrium":
                entries = await self.palace.store.list_atrium_entries(
                    status="active",
                    entry_type=params.get("entry_type"),
                )
                return ToolResult(
                    success=True,
                    output=json.dumps(
                        entries, ensure_ascii=False, default=str,
                    ),
                )
            if op == "show_atrium_entry":
                eid = params.get("entry_id")
                if not eid:
                    return ToolResult(
                        success=False, output="",
                        error="show_atrium_entry 需要 entry_id",
                    )
                e = await self.palace.store.get_atrium_entry(eid)
                if e is None:
                    return ToolResult(
                        success=False, output="",
                        error=f"no entry {eid}",
                    )
                return ToolResult(
                    success=True,
                    output=json.dumps(e, ensure_ascii=False, default=str),
                )
            if op == "stats":
                s = await self.palace.get_stats()
                return ToolResult(
                    success=True,
                    output=json.dumps(s, ensure_ascii=False),
                )
            return ToolResult(
                success=False, output="", error=f"unknown operation {op!r}",
            )
        except Exception as exc:
            return ToolResult(success=False, output="", error=str(exc))

    async def _get_raw_conversation(self, params: dict) -> ToolResult:
        drawer_id = params.get("drawer_id")
        if not drawer_id:
            return ToolResult(
                success=False, output="",
                error="get_raw_conversation 需要 drawer_id",
            )
        if drawer_id.startswith("S-"):
            s = await self.palace.store.get_south_drawer(drawer_id)
            if s is None:
                return ToolResult(
                    success=False, output="",
                    error=f"no south drawer {drawer_id}",
                )
            north_messages = []
            for nid in s["north_ref_ids"]:
                n = await self.palace.store.get_north_drawer(nid)
                if n is not None:
                    north_messages.append(n)
            return ToolResult(
                success=True,
                output=json.dumps(
                    {"south": s, "north_messages": north_messages},
                    ensure_ascii=False, default=str,
                ),
            )
        if drawer_id.startswith("N-"):
            n = await self.palace.store.get_north_drawer(drawer_id)
            if n is None:
                return ToolResult(
                    success=False, output="",
                    error=f"no north drawer {drawer_id}",
                )
            return ToolResult(
                success=True,
                output=json.dumps(n, ensure_ascii=False, default=str),
            )
        return ToolResult(
            success=False, output="",
            error=f"drawer_id must start with N- or S-, got {drawer_id!r}",
        )
