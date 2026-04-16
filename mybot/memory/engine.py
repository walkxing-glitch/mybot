"""Memory engine core — the single entry point the agent layer talks to.

This module wires together :mod:`store`, :mod:`decay`, :mod:`profile` and
:mod:`temporal` into a coherent async API:

- ``remember(content, ...)``                store a new memory
- ``recall(query, ...)``                    retrieve memories by FTS + salience
- ``get_context_for_prompt(query)``         ready-to-inject block for system prompts
- ``end_session(session_id, messages)``     post-conversation async processing
- ``get_profile_summary()``                 formatted profile for display
- ``get_stats()``                           memory counters

The engine takes a simple async ``llm_callable`` with signature::

    async def llm_call(messages: list[dict]) -> str: ...
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Sequence

from .decay import DecayConfig, DecayEngine
from .profile import ProfileManager
from .store import Memory, MemoryStore, SessionSummary
from .temporal import (
    extract_temporal_context,
    format_memories_with_time_context,
)


logger = logging.getLogger(__name__)


# Callable signature shared with the agent layer.
LLMCallable = Callable[[list[dict[str, Any]]], Awaitable[str]]


@dataclass
class MemoryEngineConfig:
    """Engine-level tunables."""

    consolidation_interval: int = 10  # run decay every N end_session() calls
    max_recall: int = 5
    min_salience: float = 0.1
    # Keep no more than this many chars of raw conversation when summarising.
    max_conversation_chars: int = 12_000


class MemoryEngine:
    """Async orchestrator over the memory subsystem."""

    def __init__(
        self,
        db_path: str | Path,
        llm_callable: LLMCallable,
        *,
        config: MemoryEngineConfig | None = None,
        decay_config: DecayConfig | None = None,
    ):
        self.db_path = Path(db_path)
        self.llm = llm_callable
        self.config = config or MemoryEngineConfig()
        self.store = MemoryStore(self.db_path)
        self.decay = DecayEngine(self.store, decay_config)
        self.profile = ProfileManager(self.store)
        self._session_counter = 0
        self._initialized = False

    # -- lifecycle ---------------------------------------------------------

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self.store.initialize()
        self._initialized = True
        logger.info("MemoryEngine initialized at %s", self.db_path)

    # -- remember ----------------------------------------------------------

    async def remember(
        self,
        content: str,
        *,
        memory_type: str = "episode",
        importance: float = 0.5,
        tags: Sequence[str] | None = None,
        session_id: str | None = None,
        temporal_context: str | None = None,
    ) -> str:
        """Store a new memory. Auto-generates ``temporal_context`` via LLM
        when not supplied. Returns the new memory id."""
        if not self._initialized:
            await self.initialize()

        content = (content or "").strip()
        if not content:
            raise ValueError("memory content must be non-empty")

        if temporal_context is None:
            temporal_context = await self._generate_temporal_context(content)

        now = datetime.utcnow()
        memory = Memory(
            id=MemoryStore.new_id(),
            content=content,
            memory_type=memory_type,
            created_at=now,
            last_accessed=now,
            access_count=0,
            salience=_clamp01(importance),  # initial salience = base importance
            base_importance=_clamp01(importance),
            tags=list(tags or []),
            source_session=session_id,
            temporal_context=temporal_context or None,
            status="active",
        )
        await self.store.insert_memory(memory)
        logger.debug("stored memory %s (type=%s)", memory.id, memory_type)
        return memory.id

    async def _generate_temporal_context(self, content: str) -> str:
        """Ask the LLM for a one-phrase temporal label. Returns "" on failure."""
        prompt = extract_temporal_context(content)
        try:
            resp = await self.llm(
                [{"role": "user", "content": prompt}]
            )
        except Exception as exc:  # pragma: no cover - depends on LLM backend
            logger.warning("temporal_context LLM call failed: %s", exc)
            return ""
        label = (resp or "").strip().strip('"').strip("'")
        # Limit to 20 chars so a chatty LLM can't pollute the DB.
        return label[:20]

    # -- recall ------------------------------------------------------------

    async def recall(
        self,
        query: str,
        *,
        limit: int | None = None,
        min_salience: float | None = None,
        include_dormant: bool = False,
    ) -> list[Memory]:
        """Search memories via FTS, filter by salience, bump access counters."""
        if not self._initialized:
            await self.initialize()

        limit = limit if limit is not None else self.config.max_recall
        min_salience = (
            min_salience if min_salience is not None else self.config.min_salience
        )

        results = await self.store.search_memories(
            query,
            status=None if include_dormant else "active",
            min_salience=min_salience,
            limit=limit,
        )

        # Touch each result — bumps access_count and last_accessed.
        for m in results:
            try:
                await self.store.touch_memory(m.id)
                m.access_count += 1
                m.last_accessed = datetime.utcnow()
            except Exception as exc:  # pragma: no cover
                logger.debug("touch_memory failed for %s: %s", m.id, exc)

        return results

    async def recall_formatted(
        self,
        query: str,
        *,
        limit: int | None = None,
        min_salience: float | None = None,
    ) -> str:
        """Same as :meth:`recall`, but returns the pre-formatted prompt block."""
        memories = await self.recall(
            query, limit=limit, min_salience=min_salience
        )
        return format_memories_with_time_context(memories, datetime.utcnow())

    # -- prompt assembly ---------------------------------------------------

    async def get_context_for_prompt(self, query: str) -> str:
        """Return the full context block (profile + relevant memories).

        The result is ready to be embedded as a system-prompt section.
        """
        if not self._initialized:
            await self.initialize()

        memories = await self.recall(query)
        memories_block = format_memories_with_time_context(
            memories, datetime.utcnow()
        )
        profile_block = await self.profile.format_profile_for_llm()
        return f"{profile_block}\n\n{memories_block}"

    # -- end_session -------------------------------------------------------

    async def end_session(
        self,
        session_id: str,
        conversation_messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Post-conversation processing.

        Steps:
          1. Generate a short session summary via LLM.
          2. Ask the LLM to extract memorable items (fact / preference /
             observation) and rate their importance.
          3. Store new memories; persist the summary.
          4. Ask the LLM for a profile diff; apply it.
          5. Every N sessions, run decay consolidation.
        """
        if not self._initialized:
            await self.initialize()

        self._session_counter += 1
        stats: dict[str, Any] = {
            "session_id": session_id,
            "new_memories": 0,
            "profile_diff": {},
            "consolidation": None,
            "summary": "",
            "topics": [],
        }

        if not conversation_messages:
            return stats

        convo_text = _render_conversation(
            conversation_messages, self.config.max_conversation_chars
        )

        # --- 1. summary ---------------------------------------------------
        summary_text, topics = await self._summarise(convo_text)
        stats["summary"] = summary_text
        stats["topics"] = topics

        # --- 2. memory extraction ----------------------------------------
        extractions = await self._extract_memorables(convo_text)

        # --- 3. persist new memories -------------------------------------
        new_ids: list[str] = []
        for item in extractions:
            try:
                mid = await self.remember(
                    content=item["content"],
                    memory_type=item.get("memory_type", "episode"),
                    importance=float(item.get("importance", 0.5)),
                    tags=item.get("tags") or [],
                    session_id=session_id,
                    temporal_context=item.get("temporal_context"),
                )
                new_ids.append(mid)
            except Exception as exc:
                logger.warning("failed to store extracted memory: %s", exc)
        stats["new_memories"] = len(new_ids)

        # Persist session summary row.
        try:
            await self.store.insert_session_summary(
                SessionSummary(
                    id=MemoryStore.new_id(),
                    session_id=session_id,
                    summary=summary_text or "(empty summary)",
                    topics=topics,
                    created_at=datetime.utcnow(),
                    memory_ids=new_ids,
                )
            )
        except Exception as exc:
            logger.warning("failed to persist session summary: %s", exc)

        # --- 4. profile diff ---------------------------------------------
        try:
            profile_block = await self.profile.format_profile_for_llm()
            diff_prompt = ProfileManager.generate_profile_diff_prompt(
                profile_block, summary_text or convo_text
            )
            raw = await self.llm(
                [{"role": "user", "content": diff_prompt}]
            )
            diff = ProfileManager.parse_diff_response(raw)
            diff_stats = await self.profile.apply_diff(diff)
            stats["profile_diff"] = diff_stats.to_dict()
        except Exception as exc:
            logger.warning("profile diff failed: %s", exc)
            stats["profile_diff"] = {"error": str(exc)}

        # --- 5. periodic consolidation -----------------------------------
        if self._session_counter % self.config.consolidation_interval == 0:
            try:
                stats["consolidation"] = await self.decay.consolidate()
            except Exception as exc:
                logger.warning("consolidation failed: %s", exc)
                stats["consolidation"] = {"error": str(exc)}

        return stats

    # -- profile + stats accessors ----------------------------------------

    async def get_profile_summary(self) -> str:
        if not self._initialized:
            await self.initialize()
        return await self.profile.format_profile_for_llm(min_confidence=0.0)

    async def get_stats(self) -> dict[str, Any]:
        if not self._initialized:
            await self.initialize()
        total = await self.store.count_memories()
        by_status = {
            status: await self.store.count_memories(status=status)
            for status in ("active", "dormant", "archived")
        }
        by_type = {
            mtype: await self.store.count_memories(memory_type=mtype)
            for mtype in ("episode", "fact", "preference", "observation")
        }
        traits = await self.profile.load_profile()
        return {
            "total_memories": total,
            "by_status": by_status,
            "by_type": by_type,
            "profile_traits": len(traits),
            "session_counter": self._session_counter,
        }

    # -- LLM helpers -------------------------------------------------------

    async def _summarise(self, convo_text: str) -> tuple[str, list[str]]:
        prompt = f"""你是一个对话总结代理。请基于下面这段对话，给出一段 150 字以内的中文摘要，并抽取 1-5 个主题标签。

对话内容：
---
{convo_text}
---

严格按下列 JSON 输出，不要加任何其他文字：

{{"summary": "...", "topics": ["...", "..."]}}
"""
        try:
            raw = await self.llm([{"role": "user", "content": prompt}])
        except Exception as exc:
            logger.warning("summary LLM call failed: %s", exc)
            return "", []

        obj = _parse_json_response(raw)
        summary = str(obj.get("summary", "")).strip() if obj else ""
        topics_raw = obj.get("topics", []) if obj else []
        topics: list[str] = [
            str(t).strip() for t in topics_raw if isinstance(t, (str, int))
        ][:5]
        return summary, topics

    async def _extract_memorables(
        self, convo_text: str
    ) -> list[dict[str, Any]]:
        prompt = f"""你是一个记忆抽取代理。从下面的对话中识别出值得长期记住的条目。

对话内容：
---
{convo_text}
---

请输出一个 JSON 数组，每个元素形如：

{{
  "content": "一句话陈述，客观第三人称",
  "memory_type": "episode|fact|preference|observation",
  "importance": 0.0-1.0,
  "tags": ["..."]   // 可选
}}

规则：
- 只在有明确信息增量时才抽取，没有就输出空数组 []
- memory_type 选择：
  · fact: 客观事实（"用户住在北京"）
  · preference: 偏好（"用户偏好中文回复"）
  · observation: 行为/状态观察（"用户今晚熬夜写代码"）
  · episode: 具体事件（"用户今天和张三开了会"）
- importance: 越关键越高。一次性琐事 0.2-0.4，明确偏好 0.6-0.8，重大决定 0.8-1.0
- 输出必须是合法 JSON 数组，不要加 markdown 代码块标记
"""
        try:
            raw = await self.llm([{"role": "user", "content": prompt}])
        except Exception as exc:
            logger.warning("extract memorables LLM call failed: %s", exc)
            return []

        items = _parse_json_array_response(raw)
        # Basic validation and filtering.
        out: list[dict[str, Any]] = []
        valid_types = {"episode", "fact", "preference", "observation"}
        for item in items:
            if not isinstance(item, dict):
                continue
            content = str(item.get("content", "")).strip()
            if not content:
                continue
            mtype = str(item.get("memory_type", "episode"))
            if mtype not in valid_types:
                mtype = "episode"
            try:
                importance = float(item.get("importance", 0.5))
            except (TypeError, ValueError):
                importance = 0.5
            tags = item.get("tags") or []
            if not isinstance(tags, list):
                tags = []
            tags = [str(t) for t in tags][:10]
            out.append(
                {
                    "content": content,
                    "memory_type": mtype,
                    "importance": _clamp01(importance),
                    "tags": tags,
                    "temporal_context": item.get("temporal_context"),
                }
            )
        return out


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _clamp01(v: float) -> float:
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v


def _render_conversation(
    messages: list[dict[str, Any]], max_chars: int
) -> str:
    """Render a conversation message list into plain text, truncated at
    ``max_chars`` from the tail (most recent kept)."""
    parts: list[str] = []
    for msg in messages:
        role = str(msg.get("role", "user"))
        content = msg.get("content", "")
        if isinstance(content, list):
            # Anthropic-style list content — join text blocks.
            text = "".join(
                (block.get("text", "") if isinstance(block, dict) else str(block))
                for block in content
            )
        else:
            text = str(content)
        text = text.strip()
        if not text:
            continue
        parts.append(f"{role}: {text}")
    blob = "\n".join(parts)
    if len(blob) > max_chars:
        blob = "…(前略)…\n" + blob[-max_chars:]
    return blob


def _parse_json_response(raw: str) -> dict[str, Any]:
    if not raw:
        return {}
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, dict) else {}
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    if start < 0:
        return {}
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start : i + 1])
                    return obj if isinstance(obj, dict) else {}
                except json.JSONDecodeError:
                    return {}
    return {}


def _parse_json_array_response(raw: str) -> list[Any]:
    if not raw:
        return []
    text = raw.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        arr = json.loads(text)
        return arr if isinstance(arr, list) else []
    except json.JSONDecodeError:
        pass
    start = text.find("[")
    if start < 0:
        return []
    depth = 0
    for i in range(start, len(text)):
        ch = text[i]
        if ch == "[":
            depth += 1
        elif ch == "]":
            depth -= 1
            if depth == 0:
                try:
                    arr = json.loads(text[start : i + 1])
                    return arr if isinstance(arr, list) else []
                except json.JSONDecodeError:
                    return []
    return []
