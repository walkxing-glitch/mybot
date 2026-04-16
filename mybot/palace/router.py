"""Router: room + drawer assignment + overflow merge (LLM-backed)."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Optional

import numpy as np

from .config import PalaceConfig
from .store import PalaceStore


logger = logging.getLogger(__name__)
LLMCallable = Callable[[list], Awaitable[str]]


@dataclass
class RoomSlot:
    room: int
    room_type: str  # 'fixed'|'dynamic'|'misc'
    room_label: str


@dataclass
class DrawerSlot:
    room: int
    drawer: int  # 1..20, -1 = overflow
    is_merge_target: bool = False


@dataclass
class MergeResult:
    target_south_id: str
    merged_summary: str


MERGE_PROMPT = """请将两段关于同一主题的摘要合并成一段连贯的中文摘要，≤200 字，保留所有关键信息，不要遗漏：

【原摘要】
{old}

【新增摘要】
{new}

只输出合并后的摘要正文，不要加任何标签或 JSON。"""


class Router:
    def __init__(
        self,
        cfg: PalaceConfig,
        store: PalaceStore,
        *,
        embedder=None,
        llm: Optional[LLMCallable] = None,
    ):
        self.cfg = cfg
        self.store = store
        self.embedder = embedder
        self.llm = llm
        self._fixed_label_to_room = {v: k for k, v in cfg.fixed_rooms.items()}

    async def assign_room(self, *, date: str, proposed_label: str) -> RoomSlot:
        rooms = await self.store.get_day_room_map(date)
        if proposed_label in self._fixed_label_to_room:
            r = self._fixed_label_to_room[proposed_label]
            return RoomSlot(room=r, room_type="fixed", room_label=proposed_label)
        for r, meta in rooms.items():
            if meta["room_type"] == "dynamic" and meta["room_label"] == proposed_label:
                return RoomSlot(
                    room=r, room_type="dynamic", room_label=proposed_label,
                )
        used = {r for r, meta in rooms.items() if meta["room_type"] == "dynamic"}
        for r in range(11, 20):
            if r not in used:
                return RoomSlot(
                    room=r, room_type="dynamic", room_label=proposed_label,
                )
        return RoomSlot(
            room=self.cfg.misc_room, room_type="misc", room_label="杂项",
        )

    async def assign_drawer(self, *, date: str, slot: RoomSlot) -> DrawerSlot:
        rooms = await self.store.get_day_room_map(date)
        count = rooms.get(slot.room, {}).get("drawer_count", 0)
        if count < 20:
            return DrawerSlot(room=slot.room, drawer=count + 1)
        return DrawerSlot(room=slot.room, drawer=-1, is_merge_target=True)

    async def merge_into_existing(
        self,
        *,
        date: str,
        slot: RoomSlot,
        new_summary: str,
        new_north_id: str,
        new_embedding,
    ) -> MergeResult:
        drawers = await self.store.list_room_south_drawers(
            date=date, room=slot.room,
        )
        if not drawers:
            raise RuntimeError(
                f"room {slot.room} has no drawers to merge into"
            )
        sims = []
        for d in drawers:
            target_emb = await self.store.get_south_embedding(d["id"])
            if target_emb is None:
                continue
            sims.append((float(np.dot(target_emb, new_embedding)), d))
        sims.sort(key=lambda x: -x[0])
        target = sims[0][1]

        merged_summary = target["summary"]
        if self.llm is not None:
            prompt = MERGE_PROMPT.format(old=target["summary"], new=new_summary)
            try:
                resp = await self.llm([{"role": "user", "content": prompt}])
                merged_summary = (resp or "").strip()[:300] or target["summary"]
            except Exception as exc:
                logger.warning("merge LLM failed: %s", exc)

        await self.store.merge_south_drawer(
            target_id=target["id"],
            new_north_id=new_north_id,
            new_summary=merged_summary,
            new_embedding=new_embedding,
        )
        await self.store.log_drawer_merge(
            target_id=target["id"],
            merged_from=[{
                "old_summary": target["summary"],
                "new_summary": new_summary,
                "new_north_id": new_north_id,
            }],
            reason="drawer_overflow",
        )
        return MergeResult(
            target_south_id=target["id"], merged_summary=merged_summary,
        )
