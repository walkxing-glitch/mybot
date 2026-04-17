"""MyBot 丽泽SOHO双塔DNA记忆系统（Memory Palace）门面。

南塔（summary + vec + BM25）+ 北塔（原始对话）+ 中庭（永久规则/偏好/事实）
See docs/superpowers/specs/2026-04-16-mybot-memory-palace-design.md
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable, Dict, List, Optional

from .atrium import AtriumManager
from .config import PalaceConfig
from .retriever import Retriever
from .store import PalaceStore
from .writer import ArchiveResult, Writer


__version__ = "0.1.0"
__all__ = ["MemoryPalace", "PalaceConfig", "ArchiveResult"]

logger = logging.getLogger(__name__)

LLMCallable = Callable[[List[Dict[str, Any]]], Awaitable[str]]


class MemoryPalace:
    """The facade that mybot agent talks to.

    Signature-compatible with the legacy MemoryEngine on
    `get_context_for_prompt` and `end_session`.
    """

    def __init__(
        self, *, cfg: PalaceConfig, llm: LLMCallable, embedder, reranker,
    ) -> None:
        self.cfg = cfg
        self.store = PalaceStore(cfg)
        self.writer = Writer(
            cfg=cfg, store=self.store, llm=llm, embedder=embedder,
        )
        self.retriever = Retriever(
            cfg=cfg, store=self.store,
            embedder=embedder, reranker=reranker,
        )
        self.atrium = AtriumManager(
            cfg=cfg, store=self.store, embedder=embedder,
        )
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self.store.initialize()
        self._initialized = True
        logger.info("MemoryPalace initialized: %s", self.cfg.db_path)

    async def close(self) -> None:
        await self.store.close()
        self._initialized = False

    async def assemble_context(
        self, user_query: str, *,
        now_year: Optional[int] = None,
        now_date: Optional[str] = None,
    ) -> str:
        if not self._initialized:
            await self.initialize()
        now_year = now_year or datetime.now(timezone.utc).year

        atrium_block = await self.atrium.assemble_block(
            query=user_query, now_year=now_year,
        )
        south_hits = await self.retriever.search(
            user_query, now_year=now_year,
        )

        parts: List[str] = []
        if atrium_block:
            parts.append(atrium_block)
        if south_hits:
            parts.append("## 📚 可能相关的过去对话（南塔·top 5）")
            for i, h in enumerate(south_hits, 1):
                parts.append(
                    f"{i}. [{h['date']} {h['room_label']}/{h['drawer_topic']}]"
                    f"（坐标 {h['id']}，可 get_raw_conversation 取原文）\n"
                    f"   {h['summary']}"
                )
        return "\n\n".join(parts)

    async def archive_session(
        self, session_id: str, messages: List[Dict[str, Any]], *,
        now_date: Optional[str] = None, now_year: Optional[int] = None,
    ) -> ArchiveResult:
        if not self._initialized:
            await self.initialize()
        return await self.writer.archive_session(
            session_id=session_id, messages=messages,
            now_date=now_date, now_year=now_year,
        )

    # --- MemoryEngine 兼容 shim ---

    async def get_context_for_prompt(self, query: str) -> str:
        return await self.assemble_context(query)

    async def end_session(
        self, session_id: str, conversation_messages: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        result = await self.archive_session(session_id, conversation_messages)
        return {
            "session_id": session_id,
            "north_ids": result.north_ids,
            "south_ids": result.south_ids,
            "atrium_ids": result.atrium_ids,
            "merge_count": result.merge_count,
        }

    async def get_stats(self) -> Dict[str, int]:
        if not self._initialized:
            await self.initialize()

        def _sync() -> Dict[str, int]:
            with self.store._sync_conn() as conn:
                def one(sql: str) -> int:
                    return conn.execute(sql).fetchone()[0]
                return {
                    "north_drawers": one("SELECT COUNT(*) FROM north_drawer"),
                    "south_drawers": one("SELECT COUNT(*) FROM south_drawer"),
                    "atrium_active": one(
                        "SELECT COUNT(*) FROM atrium_entry "
                        "WHERE status='active'",
                    ),
                    "atrium_pending": one(
                        "SELECT COUNT(*) FROM atrium_entry "
                        "WHERE status='pending'",
                    ),
                }

        return await self.store._run_sync(_sync)
