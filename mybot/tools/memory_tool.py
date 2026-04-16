"""Memory tool — exposes the memory engine to the LLM.

The memory engine implementation lives in ``mybot.memory.engine`` (built by another
agent). This tool only wraps its public methods, so it tolerates a range of
reasonable engine APIs and degrades gracefully when methods are missing.
"""

from __future__ import annotations

import json
from typing import Any

from mybot.tools.base import BaseTool, ToolResult

_ALLOWED_OPS = ("remember", "recall", "profile", "stats")
_ALLOWED_TYPES = ("episode", "fact", "preference", "observation")


def _as_text(obj: Any) -> str:
    """Render engine responses as readable text."""
    if obj is None:
        return ""
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
    except Exception:  # noqa: BLE001
        return repr(obj)


async def _maybe_await(value: Any) -> Any:
    """Await value if it's awaitable, else return as-is."""
    import inspect
    if inspect.isawaitable(value):
        return await value
    return value


class MemoryTool(BaseTool):
    """Give the LLM first-class access to its own memory."""

    name = "memory"
    description = (
        "Manage the agent's long-term memory. Operations: "
        "'remember' (store a new memory with content + memory_type + importance), "
        "'recall' (search memories by query), "
        "'profile' (summarize the current user profile), "
        "'stats' (memory counts and health)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(_ALLOWED_OPS),
                "description": "Which memory operation to run.",
            },
            "content": {
                "type": "string",
                "description": "Content to remember (for 'remember').",
            },
            "query": {
                "type": "string",
                "description": "Search query (for 'recall').",
            },
            "memory_type": {
                "type": "string",
                "enum": list(_ALLOWED_TYPES),
                "description": "Memory type (for 'remember'). Default 'observation'.",
            },
            "importance": {
                "type": "number",
                "description": "Base importance 0.0-1.0 (for 'remember'). Default 0.5.",
            },
            "limit": {
                "type": "integer",
                "description": "Max results (for 'recall'). Default 5.",
            },
        },
        "required": ["operation"],
    }

    def __init__(self, memory_engine: Any) -> None:
        if memory_engine is None:
            raise ValueError("MemoryTool requires a memory_engine instance.")
        self.engine = memory_engine

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
            if op == "remember":
                return await self._remember(params)
            if op == "recall":
                return await self._recall(params)
            if op == "profile":
                return await self._profile()
            if op == "stats":
                return await self._stats()
        except AttributeError as exc:
            return ToolResult(
                success=False,
                output="",
                error=f"Memory engine is missing a required method: {exc}",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"Memory error: {exc}")

        return ToolResult(success=False, output="", error="Unreachable branch.")

    # ---- remember -----------------------------------------------------------

    async def _remember(self, params: dict) -> ToolResult:
        content = (params.get("content") or "").strip()
        if not content:
            return ToolResult(success=False, output="", error="'content' is required for remember.")
        memory_type = (params.get("memory_type") or "observation").lower()
        if memory_type not in _ALLOWED_TYPES:
            return ToolResult(
                success=False,
                output="",
                error=f"Unknown memory_type {memory_type!r}. Allowed: {', '.join(_ALLOWED_TYPES)}.",
            )
        try:
            importance = float(params.get("importance") or 0.5)
        except (TypeError, ValueError):
            return ToolResult(success=False, output="", error="'importance' must be a number.")
        importance = max(0.0, min(importance, 1.0))

        # Try the most specific API first, then fall back.
        method = (
            getattr(self.engine, "remember", None)
            or getattr(self.engine, "store", None)
            or getattr(self.engine, "add_memory", None)
        )
        if method is None:
            return ToolResult(
                success=False,
                output="",
                error="Memory engine has no remember/store/add_memory method.",
            )

        try:
            result = method(
                content=content,
                memory_type=memory_type,
                importance=importance,
            )
        except TypeError:
            # Older engine signature? Try positional.
            result = method(content, memory_type, importance)
        result = await _maybe_await(result)

        return ToolResult(
            success=True,
            output=f"Stored {memory_type} memory (importance={importance:.2f}): {content[:120]}"
                   + (f"\n→ {_as_text(result)}" if result else ""),
        )

    # ---- recall -------------------------------------------------------------

    async def _recall(self, params: dict) -> ToolResult:
        query = (params.get("query") or "").strip()
        if not query:
            return ToolResult(success=False, output="", error="'query' is required for recall.")
        try:
            limit = int(params.get("limit") or 5)
        except (TypeError, ValueError):
            return ToolResult(success=False, output="", error="'limit' must be an integer.")
        limit = max(1, min(limit, 50))

        method = (
            getattr(self.engine, "recall", None)
            or getattr(self.engine, "search", None)
            or getattr(self.engine, "search_memories", None)
        )
        if method is None:
            return ToolResult(
                success=False,
                output="",
                error="Memory engine has no recall/search/search_memories method.",
            )

        try:
            result = method(query=query, limit=limit)
        except TypeError:
            result = method(query, limit)
        result = await _maybe_await(result)

        if not result:
            return ToolResult(success=True, output=f"No memories matched {query!r}.")

        rendered = _as_text(result)
        return ToolResult(
            success=True,
            output=f"Recall for {query!r} (top {limit}):\n{rendered}",
        )

    # ---- profile ------------------------------------------------------------

    async def _profile(self) -> ToolResult:
        method = (
            getattr(self.engine, "profile", None)
            or getattr(self.engine, "get_profile", None)
            or getattr(self.engine, "summarize_profile", None)
        )
        if method is None:
            return ToolResult(
                success=False,
                output="",
                error="Memory engine has no profile/get_profile/summarize_profile method.",
            )
        result = await _maybe_await(method())
        if not result:
            return ToolResult(success=True, output="Profile is empty (no traits recorded yet).")
        return ToolResult(success=True, output=_as_text(result))

    # ---- stats --------------------------------------------------------------

    async def _stats(self) -> ToolResult:
        method = (
            getattr(self.engine, "stats", None)
            or getattr(self.engine, "get_stats", None)
            or getattr(self.engine, "statistics", None)
        )
        if method is None:
            return ToolResult(
                success=False,
                output="",
                error="Memory engine has no stats/get_stats/statistics method.",
            )
        result = await _maybe_await(method())
        return ToolResult(success=True, output=_as_text(result) or "(no stats)")


# Note: MemoryTool requires a memory_engine instance, so we cannot create a
# default here. The agent assembles it at startup and adds to its tool registry.
tools: list[BaseTool] = []
