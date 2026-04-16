"""Forgetting / salience decay for the memory engine.

    salience = base_importance
             * recency_decay
             * access_boost
             * relevance_multiplier

where

    recency_decay        = exp(-lambda * t_days)
    lambda               = ln(2) / half_life_days
    access_boost         = 1 + 0.2 * ln(1 + access_count)
    relevance_multiplier ∈ [0.5, 2.0], supplied externally.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import datetime
from typing import Callable, Iterable, Mapping

from .store import Memory, MemoryStore


DEFAULT_HALF_LIVES: Mapping[str, float] = {
    "episode": 30.0,
    "observation": 30.0,
    "fact": 180.0,
    "preference": 365.0,
}

DORMANT_THRESHOLD = 0.1
ARCHIVED_THRESHOLD = 0.01

RELEVANCE_MIN = 0.5
RELEVANCE_MAX = 2.0


@dataclass
class DecayConfig:
    """Tunable parameters for the decay model."""

    half_lives: Mapping[str, float] | None = None
    dormant_threshold: float = DORMANT_THRESHOLD
    archived_threshold: float = ARCHIVED_THRESHOLD
    default_half_life_days: float = 30.0

    def __post_init__(self) -> None:
        merged = dict(DEFAULT_HALF_LIVES)
        if self.half_lives:
            merged.update(self.half_lives)
        self.half_lives = merged

    def half_life_for(self, memory_type: str) -> float:
        return float(self.half_lives.get(memory_type, self.default_half_life_days))


# ---------------------------------------------------------------------------
# Pure math
# ---------------------------------------------------------------------------


def recency_decay(
    created_at: datetime, now: datetime, half_life_days: float
) -> float:
    """exp(-lambda * t_days), never below 0."""
    if half_life_days <= 0:
        return 1.0
    dt = (now - created_at).total_seconds() / 86400.0
    dt = max(dt, 0.0)
    lam = math.log(2.0) / half_life_days
    return math.exp(-lam * dt)


def access_boost(access_count: int) -> float:
    return 1.0 + 0.2 * math.log1p(max(access_count, 0))


def clamp_relevance(multiplier: float) -> float:
    if multiplier < RELEVANCE_MIN:
        return RELEVANCE_MIN
    if multiplier > RELEVANCE_MAX:
        return RELEVANCE_MAX
    return multiplier


def compute_salience(
    memory: Memory,
    now: datetime,
    config: DecayConfig,
    relevance_multiplier: float = 1.0,
) -> float:
    """Pure salience computation. Result clamped to [0.0, 1.0]."""
    hl = config.half_life_for(memory.memory_type)
    r = recency_decay(memory.created_at, now, hl)
    a = access_boost(memory.access_count)
    m = clamp_relevance(relevance_multiplier)
    raw = memory.base_importance * r * a * m
    if raw < 0.0:
        return 0.0
    if raw > 1.0:
        return 1.0
    return raw


RelevanceFn = Callable[[Memory], float]


def _default_relevance(_memory: Memory) -> float:
    return 1.0


# ---------------------------------------------------------------------------
# DecayEngine
# ---------------------------------------------------------------------------


class DecayEngine:
    """Computes and persists salience + status transitions."""

    def __init__(
        self,
        store: MemoryStore,
        config: DecayConfig | None = None,
    ):
        self.store = store
        self.config = config or DecayConfig()

    async def recompute_all(
        self,
        *,
        relevance_fn: RelevanceFn | None = None,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Recompute salience for every active memory in one batch."""
        now = now or datetime.utcnow()
        relevance_fn = relevance_fn or _default_relevance

        memories = await self.store.list_memories(
            status="active", limit=10_000, order_by="created_at ASC"
        )
        updates: list[tuple[str, float]] = []
        for m in memories:
            sal = compute_salience(m, now, self.config, relevance_fn(m))
            updates.append((m.id, sal))
        count = await self.store.batch_update_salience(updates)
        return {"active_scanned": len(memories), "updated": count}

    async def consolidate(
        self,
        *,
        relevance_fn: RelevanceFn | None = None,
        now: datetime | None = None,
    ) -> dict[str, int]:
        """Recompute salience and transition statuses based on thresholds.

        - active → dormant/archived on drop
        - dormant → active on re-boost
        """
        now = now or datetime.utcnow()
        relevance_fn = relevance_fn or _default_relevance

        active = await self.store.list_memories(
            status="active", limit=10_000, order_by="created_at ASC"
        )
        dormant = await self.store.list_memories(
            status="dormant", limit=10_000, order_by="created_at ASC"
        )

        salience_updates: list[tuple[str, float]] = []
        status_updates: list[tuple[str, str]] = []

        moved_dormant = 0
        moved_archived = 0
        revived = 0

        for m in active:
            sal = compute_salience(m, now, self.config, relevance_fn(m))
            salience_updates.append((m.id, sal))
            if sal < self.config.archived_threshold:
                status_updates.append((m.id, "archived"))
                moved_archived += 1
            elif sal < self.config.dormant_threshold:
                status_updates.append((m.id, "dormant"))
                moved_dormant += 1

        for m in dormant:
            sal = compute_salience(m, now, self.config, relevance_fn(m))
            salience_updates.append((m.id, sal))
            if sal < self.config.archived_threshold:
                status_updates.append((m.id, "archived"))
                moved_archived += 1
            elif sal >= self.config.dormant_threshold:
                status_updates.append((m.id, "active"))
                revived += 1

        await self.store.batch_update_salience(salience_updates)
        await self.store.batch_update_status(status_updates)

        return {
            "active_scanned": len(active),
            "dormant_scanned": len(dormant),
            "moved_dormant": moved_dormant,
            "moved_archived": moved_archived,
            "revived": revived,
            "salience_updates": len(salience_updates),
            "status_updates": len(status_updates),
        }

    async def recompute_for(
        self,
        memory_ids: Iterable[str],
        *,
        relevance_fn: RelevanceFn | None = None,
        now: datetime | None = None,
    ) -> int:
        """Recompute salience for a specific subset of memories."""
        now = now or datetime.utcnow()
        relevance_fn = relevance_fn or _default_relevance

        updates: list[tuple[str, float]] = []
        for mid in memory_ids:
            m = await self.store.get_memory(mid)
            if not m:
                continue
            sal = compute_salience(m, now, self.config, relevance_fn(m))
            updates.append((m.id, sal))
        return await self.store.batch_update_salience(updates)
