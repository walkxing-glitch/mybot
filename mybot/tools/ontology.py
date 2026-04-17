"""Ontology tool — query the myontology/ontology-engine knowledge graph.

只对接 myontology 的 ontology-engine（本机 :8003）。ontology-platform 已被用户停止开发。

路径来自 http://localhost:8003/openapi.json（2026-04-16 实测）：
    /api/objects/search            GET  q/type/property_key/limit/offset
    /api/objects/{id}              GET  object 详情
    /api/objects/stats             GET  跨本体全局统计
    /api/person/{oid}/{name}       GET  按中文名定位人及其关系
    /api/relators/?ontology_id=    GET  关系列表（支持按参与者过滤）
    /api/overview/{oid}            GET  本体总览（约 650 字节）
    /api/dashboard/{oid}/insights  GET  dashboard 洞察（约 9KB）

ontology_id 当前只有一个：`362a5ce1-29ca-4b4b-8bd0-29c122435bd3`（"邢智强 2025"）。
可通过环境变量 MYBOT_ONTOLOGY_ID 覆盖；base_url 同理用 MYBOT_ONTOLOGY_API_URL。
"""

from __future__ import annotations

import json
import os
from typing import Any

import httpx

from mybot.tools.base import BaseTool, ToolResult

DEFAULT_BASE_URL = os.environ.get("MYBOT_ONTOLOGY_API_URL", "http://localhost:8003")
DEFAULT_ONTOLOGY_ID = os.environ.get(
    "MYBOT_ONTOLOGY_ID", "362a5ce1-29ca-4b4b-8bd0-29c122435bd3"
)
DEFAULT_TIMEOUT = 30.0  # get_overview 冷启约 22s，留足余量

_ALLOWED_OPS = (
    "search",
    "get_object",
    "get_stats",
    "find_person",
    "list_relators",
    "get_overview",
    "get_insights",
    # —— 认知层端点（整合 place+person+wechat+financial+photos）
    "list_months",
    "get_month",
    "get_month_narrative",
    "get_narrative",
    "get_habits",
    "get_geography",
)


class OntologyTool(BaseTool):
    """HTTP client for myontology/ontology-engine (:8003)."""

    name = "ontology"
    description = (
        "查询本体论知识图谱（人/地点/事件/关系/习惯/情境）。核心操作：\n"
        "— search / get_object / get_stats：搜实体、看详情、全局类型统计\n"
        "— find_person(name)：按中文名找人及其所有关系（财务往来、共识、沟通）\n"
        "— list_relators：列 805 条语义关系（如 financial:credit_cycling）\n"
        "— list_months：列出有数据的月份及事件数量（选月份的索引）\n"
        "— get_month(month='YYYY-MM')：**某月聚合视图**，含 places/people/wechat 会话/"
        "financial/events/photos（4KB/月，最常用）\n"
        "— get_month_narrative(month)：某月的自然语言叙述\n"
        "— get_narrative：整体人生叙事（分章节）\n"
        "— get_habits：习惯模式分析\n"
        "— get_geography：地点×消费×居住×出行融合视图（101KB，按需用）\n"
        "— get_overview / get_insights：本体总览 / dashboard 洞察"
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(_ALLOWED_OPS),
                "description": "调用的操作名。",
            },
            "query": {
                "type": "string",
                "description": "搜索关键词（search 用）。",
            },
            "entity_type": {
                "type": "string",
                "description": "对象类型过滤，如 person/place/event/employment/role (search 用)。",
            },
            "object_id": {
                "type": "string",
                "description": "对象 UUID（get_object 用）。",
            },
            "name": {
                "type": "string",
                "description": "人名，支持中文（find_person 用）。",
            },
            "participant_id": {
                "type": "string",
                "description": "参与者 UUID，过滤只看该实体参与的关系（list_relators 用）。",
            },
            "month": {
                "type": "string",
                "description": "月份，格式 'YYYY-MM'（get_month / get_month_narrative 用）。",
            },
            "limit": {
                "type": "integer",
                "description": "返回条数上限，默认 10。",
            },
        },
        "required": ["operation"],
    }

    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        ontology_id: str = DEFAULT_ONTOLOGY_ID,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.ontology_id = ontology_id
        self.timeout = float(timeout)

    # ---------------------------------------------------------------- helpers

    async def _get(
        self, path: str, params: dict[str, Any] | None = None
    ) -> ToolResult:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                resp = await client.get(url, params=params or {})
        except httpx.ConnectError as exc:
            return ToolResult(
                success=False,
                output="",
                error=(
                    f"Cannot reach ontology API at {self.base_url}: {exc}. "
                    "myontology/ontology-engine 是否在 :8003 上运行？"
                ),
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
                error=f"Ontology API {resp.status_code}: {resp.text[:500]}",
            )

        try:
            data = resp.json()
            pretty = json.dumps(data, ensure_ascii=False, indent=2)
        except ValueError:
            pretty = resp.text
        return ToolResult(success=True, output=pretty)

    # -------------------------------------------------------------- operations

    async def execute(self, **params) -> ToolResult:
        op = params.get("operation")
        if op not in _ALLOWED_OPS:
            return ToolResult(
                success=False,
                output="",
                error=f"Unknown operation {op!r}. Allowed: {', '.join(_ALLOWED_OPS)}.",
            )

        limit = params.get("limit") or 10

        try:
            if op == "search":
                query = params.get("query")
                qp: dict[str, Any] = {"limit": limit}
                if query:
                    qp["q"] = query
                if params.get("entity_type"):
                    qp["type"] = params["entity_type"]
                return await self._get("/api/objects/search", qp)

            if op == "get_object":
                oid = params.get("object_id")
                if not oid:
                    return ToolResult(
                        success=False,
                        output="",
                        error="'object_id' is required for get_object.",
                    )
                return await self._get(f"/api/objects/{oid}")

            if op == "get_stats":
                return await self._get("/api/objects/stats")

            if op == "find_person":
                name = params.get("name")
                if not name:
                    return ToolResult(
                        success=False,
                        output="",
                        error="'name' is required for find_person.",
                    )
                # path param 包含中文，httpx 会自动 URL-encode
                return await self._get(
                    f"/api/person/{self.ontology_id}/{name}"
                )

            if op == "list_relators":
                qp2: dict[str, Any] = {
                    "ontology_id": self.ontology_id,
                    "limit": limit,
                }
                pid = params.get("participant_id")
                if pid:
                    # 路由是 /api/relators/by-participants，按参与者过滤
                    return await self._get(
                        "/api/relators/by-participants",
                        {**qp2, "participant_id": pid},
                    )
                return await self._get("/api/relators/", qp2)

            if op == "get_overview":
                return await self._get(f"/api/overview/{self.ontology_id}")

            if op == "get_insights":
                return await self._get(
                    f"/api/dashboard/{self.ontology_id}/insights"
                )

            # ---- 认知层端点（situation / narrative / habits / geography）

            if op == "list_months":
                return await self._get(f"/api/situation/{self.ontology_id}/months")

            if op == "get_month":
                month = params.get("month")
                if not month:
                    return ToolResult(
                        success=False,
                        output="",
                        error="'month' (YYYY-MM) is required for get_month.",
                    )
                return await self._get(
                    f"/api/situation/{self.ontology_id}/{month}"
                )

            if op == "get_month_narrative":
                month = params.get("month")
                if not month:
                    return ToolResult(
                        success=False,
                        output="",
                        error="'month' (YYYY-MM) is required for get_month_narrative.",
                    )
                return await self._get(
                    f"/api/situation/{self.ontology_id}/{month}/narrative"
                )

            if op == "get_narrative":
                return await self._get(f"/api/narrative/{self.ontology_id}")

            if op == "get_habits":
                return await self._get(f"/api/habits/{self.ontology_id}")

            if op == "get_geography":
                return await self._get(
                    f"/api/lebenswelt/{self.ontology_id}/geography"
                )

        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False, output="", error=f"Unhandled error: {exc}"
            )

        return ToolResult(success=False, output="", error="Unreachable branch.")


tools = [OntologyTool()]
