"""Writer: archive_session orchestrator."""
from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Awaitable, Callable, List, Optional

from .chunker import Chunker, _parse_json_array
from .config import PalaceConfig
from .router import RoomSlot, Router
from .store import PalaceStore


logger = logging.getLogger(__name__)
LLMCallable = Callable[[list], Awaitable[str]]


EXPLICIT_MARKERS = [
    "记住", "以后别", "我偏好", "我的 ", "我是", "请记",
    "不要再", "永远别", "永远不要", "以后不要",
]


EXTRACT_EXPLICIT_PROMPT = """以下是用户消息。识别其中明确要求被长期记住的"规则/偏好/事实"。

用户消息：
---
{msgs}
---

严格输出 JSON 数组，每个元素：
{{"entry_type": "rule|preference|fact", "content": "一句话客观陈述"}}

若无则输出 []。不要加 markdown。"""


@dataclass
class ArchiveResult:
    north_ids: List[str] = field(default_factory=list)
    south_ids: List[str] = field(default_factory=list)
    atrium_ids: List[str] = field(default_factory=list)
    merge_count: int = 0


class Writer:
    def __init__(
        self,
        *,
        cfg: PalaceConfig,
        store: PalaceStore,
        llm: LLMCallable,
        embedder,
    ):
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.embedder = embedder
        self.chunker = Chunker(llm=llm)
        self.router = Router(cfg=cfg, store=store, embedder=embedder, llm=llm)

    async def archive_session(
        self,
        *,
        session_id: str,
        messages: List[dict],
        now_date: Optional[str] = None,
        now_year: Optional[int] = None,
    ) -> ArchiveResult:
        if not messages:
            return ArchiveResult()
        if now_date is None:
            now_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if now_year is None:
            now_year = int(now_date[:4])
        floor = _day_of_year(now_date)

        chunks = await self.chunker.chunk_and_summarise(messages)
        if not chunks:
            logger.info("archive: no chunks for session %s", session_id)
            return ArchiveResult()

        result = ArchiveResult()

        for chunk in chunks:
            slot = await self.router.assign_room(
                date=now_date, proposed_label=chunk.proposed_room_label,
            )
            drawer_slot = await self.router.assign_drawer(
                date=now_date, slot=slot,
            )
            sub_messages = [
                messages[i] for i in chunk.msg_indices if 0 <= i < len(messages)
            ]

            if drawer_slot.drawer > 0:
                nid = await self.store.insert_north_drawer(
                    year=now_year, floor=floor, room=slot.room,
                    drawer=drawer_slot.drawer, date=now_date,
                    raw_messages=sub_messages,
                )
                emb = self.embedder.encode(chunk.summary)[0]
                sid = await self.store.insert_south_drawer(
                    year=now_year, floor=floor, room=slot.room,
                    drawer=drawer_slot.drawer, date=now_date,
                    north_ref_ids=[nid],
                    room_type=slot.room_type, room_label=slot.room_label,
                    drawer_topic=chunk.drawer_topic,
                    summary=chunk.summary, keywords=chunk.keywords,
                    embedding=emb,
                )
                await self.store.upsert_day_room(
                    date=now_date, room=slot.room,
                    room_type=slot.room_type, room_label=slot.room_label,
                    drawer_count=drawer_slot.drawer,
                )
                result.north_ids.append(nid)
                result.south_ids.append(sid)
            else:
                # Room full → store raw_messages in misc room, merge summary
                misc_slot = RoomSlot(
                    self.cfg.misc_room, "misc", "杂项(溢出)",
                )
                misc_drawer = await self.router.assign_drawer(
                    date=now_date, slot=misc_slot,
                )
                if misc_drawer.drawer < 0:
                    logger.warning(
                        "misc room also full; dropping chunk: %s",
                        chunk.drawer_topic,
                    )
                    continue
                nid = await self.store.insert_north_drawer(
                    year=now_year, floor=floor, room=misc_slot.room,
                    drawer=misc_drawer.drawer, date=now_date,
                    raw_messages=sub_messages,
                )
                emb = self.embedder.encode(chunk.summary)[0]
                await self.router.merge_into_existing(
                    date=now_date, slot=slot,
                    new_summary=chunk.summary,
                    new_north_id=nid,
                    new_embedding=emb,
                )
                await self.store.upsert_day_room(
                    date=now_date, room=misc_slot.room,
                    room_type=misc_slot.room_type,
                    room_label=misc_slot.room_label,
                    drawer_count=misc_drawer.drawer,
                )
                result.north_ids.append(nid)
                result.merge_count += 1

        await self._maybe_extract_explicit(messages, result)
        return result

    async def _maybe_extract_explicit(
        self, messages: List[dict], result: ArchiveResult,
    ) -> None:
        user_msgs = [
            str(m.get("content", "")) for m in messages
            if m.get("role") == "user"
        ]
        if not any(k in m for m in user_msgs for k in EXPLICIT_MARKERS):
            return
        prompt = EXTRACT_EXPLICIT_PROMPT.format(msgs="\n".join(user_msgs))
        try:
            raw = await self.llm([{"role": "user", "content": prompt}])
        except Exception as exc:
            logger.warning("explicit extract LLM failed: %s", exc)
            return
        items = _parse_json_array(raw)
        for it in items:
            if not isinstance(it, dict):
                continue
            content = str(it.get("content", "")).strip()
            etype = str(it.get("entry_type", "fact")).strip()
            if not content or etype not in {"rule", "preference", "fact"}:
                continue
            if _hits_blacklist(content, self.cfg.guards.blacklist_patterns):
                logger.info(
                    "atrium explicit rejected (code blacklist): %s",
                    content[:50],
                )
                continue
            try:
                emb = (
                    self.embedder.encode(content)[0] if etype == "fact" else None
                )
                aid = await self.store.insert_atrium_entry(
                    id=str(uuid.uuid4()),
                    entry_type=etype, content=content,
                    source_type="explicit", status="active",
                    confidence=0.95, embedding=emb,
                )
                result.atrium_ids.append(aid)
            except Exception as exc:
                logger.info("atrium insert failed: %s", exc)


def _hits_blacklist(text: str, patterns: List[str]) -> bool:
    return any(p in text for p in patterns)


def _day_of_year(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    doy = dt.timetuple().tm_yday
    return 365 if doy == 366 else doy
