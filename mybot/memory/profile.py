"""User profile evolution.

The profile is a small set of structured traits grouped by dimension:
behavior / interest / decision_style / social / focus.

After every conversation, we ask the LLM to produce a JSON **diff** relative
to the current profile. This module applies the diff against the store.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from .store import MemoryStore, ProfileTrait


DIMENSIONS = ("behavior", "interest", "decision_style", "social", "focus")
TRENDS = {"rising", "stable", "declining"}
DIFF_OPS = {"update", "strengthen", "weaken", "new_insight", "trend_change"}


@dataclass
class DiffStats:
    updated: int = 0
    strengthened: int = 0
    weakened: int = 0
    created: int = 0
    trend_changed: int = 0
    skipped: int = 0

    def to_dict(self) -> dict[str, int]:
        return {
            "updated": self.updated,
            "strengthened": self.strengthened,
            "weakened": self.weakened,
            "created": self.created,
            "trend_changed": self.trend_changed,
            "skipped": self.skipped,
        }


class ProfileManager:
    """Owns profile load / render / diff-apply logic."""

    def __init__(self, store: MemoryStore):
        self.store = store

    # -- load / render -----------------------------------------------------

    async def load_profile(
        self, *, min_confidence: float = 0.0
    ) -> list[ProfileTrait]:
        return await self.store.list_traits(min_confidence=min_confidence)

    async def format_profile_for_llm(
        self, *, min_confidence: float = 0.3
    ) -> str:
        traits = await self.load_profile(min_confidence=min_confidence)
        if not traits:
            return "[用户画像] 暂无记录。"

        grouped: dict[str, list[ProfileTrait]] = {d: [] for d in DIMENSIONS}
        for t in traits:
            grouped.setdefault(t.dimension, []).append(t)

        lines: list[str] = ["[用户画像]"]
        for dim in list(DIMENSIONS) + [
            d for d in grouped if d not in DIMENSIONS
        ]:
            items = grouped.get(dim) or []
            if not items:
                continue
            lines.append(f"- {dim}:")
            for t in items:
                trend_marker = {
                    "rising": " ↑",
                    "declining": " ↓",
                    "stable": "",
                }.get(t.trend, "")
                lines.append(
                    f"    · {t.key}: {t.value} "
                    f"(置信度 {t.confidence:.2f}, 证据 {t.evidence_count}{trend_marker})"
                )
        return "\n".join(lines)

    # -- diff application --------------------------------------------------

    async def apply_diff(self, diff: dict[str, Any]) -> DiffStats:
        """Apply a structured diff produced by the LLM.

        Expected shape::

            {
              "update":        [{"dimension", "key", "value", "confidence"?}],
              "strengthen":    [{"dimension", "key", "delta"?}],
              "weaken":        [{"dimension", "key", "delta"?}],
              "new_insight":   [{"dimension", "key", "value",
                                 "confidence"?, "trend"?}],
              "trend_change":  [{"dimension", "key", "trend"}]
            }
        """
        stats = DiffStats()
        if not isinstance(diff, dict):
            return stats

        now = datetime.utcnow()
        for op in DIFF_OPS:
            items = diff.get(op) or []
            if not isinstance(items, list):
                continue
            for item in items:
                if not isinstance(item, dict):
                    stats.skipped += 1
                    continue
                try:
                    await self._apply_one(op, item, now, stats)
                except Exception:
                    stats.skipped += 1
        return stats

    async def _apply_one(
        self,
        op: str,
        item: dict[str, Any],
        now: datetime,
        stats: DiffStats,
    ) -> None:
        dimension = str(item.get("dimension") or "").strip()
        key = str(item.get("key") or "").strip()
        if not dimension or not key:
            stats.skipped += 1
            return

        existing = await self.store.get_trait(dimension, key)

        if op == "update":
            if not existing:
                if "value" not in item:
                    stats.skipped += 1
                    return
                await self._insert_new(dimension, key, item, now)
                stats.created += 1
                return
            existing.value = str(item.get("value", existing.value))
            if "confidence" in item:
                existing.confidence = _clamp01(float(item["confidence"]))
            existing.evidence_count += 1
            existing.last_updated = now
            await self.store.upsert_trait(existing)
            stats.updated += 1

        elif op == "strengthen":
            if not existing:
                stats.skipped += 1
                return
            delta = float(item.get("delta", 0.1))
            delta = max(0.0, min(delta, 0.3))
            existing.confidence = _clamp01(existing.confidence + delta)
            existing.evidence_count += 1
            existing.last_updated = now
            await self.store.upsert_trait(existing)
            stats.strengthened += 1

        elif op == "weaken":
            if not existing:
                stats.skipped += 1
                return
            delta = float(item.get("delta", 0.1))
            delta = max(0.0, min(delta, 0.3))
            existing.confidence = _clamp01(existing.confidence - delta)
            existing.last_updated = now
            await self.store.upsert_trait(existing)
            stats.weakened += 1

        elif op == "new_insight":
            if "value" not in item:
                stats.skipped += 1
                return
            if existing:
                existing.value = str(item["value"])
                existing.confidence = _clamp01(
                    max(
                        existing.confidence,
                        float(item.get("confidence", 0.5)),
                    )
                )
                existing.evidence_count += 1
                existing.last_updated = now
                await self.store.upsert_trait(existing)
                stats.updated += 1
                return
            await self._insert_new(dimension, key, item, now)
            stats.created += 1

        elif op == "trend_change":
            trend = str(item.get("trend", "stable")).strip()
            if trend not in TRENDS:
                stats.skipped += 1
                return
            if not existing:
                stats.skipped += 1
                return
            if existing.trend == trend:
                stats.skipped += 1
                return
            existing.trend = trend
            existing.last_updated = now
            await self.store.upsert_trait(existing)
            stats.trend_changed += 1
        else:
            stats.skipped += 1

    async def _insert_new(
        self,
        dimension: str,
        key: str,
        item: dict[str, Any],
        now: datetime,
    ) -> None:
        trend = str(item.get("trend", "stable"))
        if trend not in TRENDS:
            trend = "stable"
        trait = ProfileTrait(
            id=MemoryStore.new_id(),
            dimension=dimension,
            key=key,
            value=str(item["value"]),
            confidence=_clamp01(float(item.get("confidence", 0.5))),
            evidence_count=1,
            first_observed=now,
            last_updated=now,
            trend=trend,
        )
        await self.store.upsert_trait(trait)

    # -- LLM prompting -----------------------------------------------------

    @staticmethod
    def generate_profile_diff_prompt(
        profile: str,
        conversation_summary: str,
    ) -> str:
        """Render the prompt that asks the LLM for a structured profile diff."""
        return f"""你是一个用户画像维护代理。

下面是当前的用户画像（按维度分组，每条带置信度和趋势）：

---
{profile}
---

下面是最近一次对话的摘要：

---
{conversation_summary}
---

请基于这次对话的新信息，输出一份对用户画像的结构化 diff，严格使用下面的 JSON 格式，不要输出任何其他文字：

{{
  "update":        [{{"dimension": "...", "key": "...", "value": "...", "confidence": 0.0-1.0 (可选)}}],
  "strengthen":    [{{"dimension": "...", "key": "...", "delta": 0.1-0.3 (可选, 默认 0.1)}}],
  "weaken":        [{{"dimension": "...", "key": "...", "delta": 0.1-0.3 (可选, 默认 0.1)}}],
  "new_insight":   [{{"dimension": "...", "key": "...", "value": "...", "confidence": 0.0-1.0, "trend": "rising|stable|declining"}}],
  "trend_change":  [{{"dimension": "...", "key": "...", "trend": "rising|stable|declining"}}]
}}

原则：
- dimension 必须来自: behavior / interest / decision_style / social / focus
- 保守判断：只在对话里有明确证据时才更新/新增
- 矛盾时优先标记 weaken 或 trend_change，而不是直接 update
- 没有变化时，返回全空数组的 JSON，不要编造
- 输出必须是合法 JSON，不要加 ```json``` 代码块标记
"""

    @staticmethod
    def parse_diff_response(raw: str) -> dict[str, Any]:
        """Parse a diff JSON returned by the LLM. Empty dict on failure."""
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


def _clamp01(v: float) -> float:
    if v < 0.0:
        return 0.0
    if v > 1.0:
        return 1.0
    return v
