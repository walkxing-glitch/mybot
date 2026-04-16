"""记忆引擎包：对外导出核心 API。

具体实现分散在：
- engine.py   检索/存入/整理
- profile.py  用户画像演化
- decay.py    遗忘衰减
- temporal.py 时间推理
- store.py    SQLite 持久化
"""

from __future__ import annotations

from .decay import (
    DEFAULT_HALF_LIVES,
    DecayConfig,
    DecayEngine,
    compute_salience,
)
from .engine import LLMCallable, MemoryEngine, MemoryEngineConfig
from .profile import DiffStats, ProfileManager
from .store import Memory, MemoryStore, ProfileTrait, SessionSummary
from .temporal import (
    extract_temporal_context,
    filter_by_temporal_pattern,
    format_memories_with_time_context,
    humanize_delta,
)

__all__ = [
    # engine
    "MemoryEngine",
    "MemoryEngineConfig",
    "LLMCallable",
    # store
    "MemoryStore",
    "Memory",
    "ProfileTrait",
    "SessionSummary",
    # decay
    "DecayEngine",
    "DecayConfig",
    "DEFAULT_HALF_LIVES",
    "compute_salience",
    # profile
    "ProfileManager",
    "DiffStats",
    # temporal
    "format_memories_with_time_context",
    "extract_temporal_context",
    "filter_by_temporal_pattern",
    "humanize_delta",
]
