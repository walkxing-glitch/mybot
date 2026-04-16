"""记忆引擎包：对外导出 MemoryEngine。

具体实现分散在：
- engine.py  检索/存入/整理
- profile.py 用户画像演化
- decay.py   遗忘衰减
- temporal.py 时间推理
- store.py   SQLite 持久化
"""

from __future__ import annotations

try:
    from mybot.memory.engine import MemoryEngine  # type: ignore[attr-defined]
except ImportError:  # pragma: no cover - 实现文件尚未创建时允许包可 import
    MemoryEngine = None  # type: ignore[assignment]

__all__ = ["MemoryEngine"]
