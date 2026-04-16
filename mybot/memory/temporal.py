"""Cross-session temporal reasoning helpers."""

from __future__ import annotations

from datetime import datetime
from typing import Iterable, Sequence

from .store import Memory


# ---------------------------------------------------------------------------
# Relative time rendering
# ---------------------------------------------------------------------------


def humanize_delta(past: datetime, now: datetime) -> str:
    """Return a zh-CN relative-time phrase ('31天前', '刚刚', '2小时前')."""
    delta = now - past
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "未来"
    if seconds < 60:
        return "刚刚"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}分钟前"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}小时前"
    days = hours // 24
    if days < 30:
        return f"{days}天前"
    months = days // 30
    if months < 12:
        return f"{months}个月前"
    years = days // 365
    return f"{years}年前"


# ---------------------------------------------------------------------------
# Formatting
# ---------------------------------------------------------------------------


def format_memories_with_time_context(
    memories: Sequence[Memory],
    current_time: datetime | None = None,
) -> str:
    """Render memories as a numbered, time-annotated list for prompt injection.

    Adds automatic system notes for contradictions and declining trends.
    """
    if not memories:
        return "[相关记忆] 暂无。"

    now = current_time or datetime.utcnow()
    lines: list[str] = ["系统检索到以下相关记忆（按相关度排序）："]

    for i, m in enumerate(memories, 1):
        rel = humanize_delta(m.created_at, now)
        date_str = m.created_at.strftime("%Y-%m-%d")
        tag_str = f" tags={m.tags}" if m.tags else ""
        ctx_str = (
            f" temporal_context={m.temporal_context!r}"
            if m.temporal_context
            else ""
        )
        lines.append(
            f"\n{i}. [{date_str}, {rel}] "
            f"[{m.memory_type}] {m.content}"
        )
        lines.append(
            f"   salience: {m.salience:.2f}, 访问次数: {m.access_count}"
            f"{tag_str}{ctx_str}"
        )

    notes = _collect_notes(memories, now)
    if notes:
        lines.append("")
        lines.append("注意：")
        for note in notes:
            lines.append(f"- {note}")

    return "\n".join(lines)


def _collect_notes(memories: Sequence[Memory], now: datetime) -> list[str]:
    notes: list[str] = []

    negation_markers = ["不要", "不接", "放弃", "停止", "取消", "拒绝"]
    affirmation_markers = ["决定", "继续", "坚持", "开始", "上线", "采用"]

    texts = [m.content for m in memories]
    for i in range(len(texts)):
        for j in range(i + 1, len(texts)):
            a, b = texts[i], texts[j]
            a_neg = any(tok in a for tok in negation_markers)
            b_aff = any(tok in b for tok in affirmation_markers)
            b_neg = any(tok in b for tok in negation_markers)
            a_aff = any(tok in a for tok in affirmation_markers)
            if (a_neg and b_aff) or (a_aff and b_neg):
                notes.append(
                    f"如果用户当前意图与记忆 #{i+1} 和 #{j+1} "
                    "中的历史决策矛盾，请主动指出变化。"
                )
                break
        else:
            continue
        break

    declining = [
        m
        for m in memories
        if m.temporal_context and "declining" in m.temporal_context.lower()
    ]
    if declining:
        notes.append(
            "标记为 declining trend 的画像 trait 可能已不准确，"
            "在回答前请重新求证。"
        )

    if memories:
        oldest = min(memories, key=lambda m: m.created_at)
        age_days = (now - oldest.created_at).total_seconds() / 86400.0
        if age_days > 180:
            notes.append(
                f"最旧的记忆已 {int(age_days)} 天前，"
                "事实可能过时，建议向用户确认。"
            )

    return notes


# ---------------------------------------------------------------------------
# LLM prompt for temporal labelling
# ---------------------------------------------------------------------------


def extract_temporal_context(content: str) -> str:
    """Return the LLM prompt that asks for a temporal label.

    The LLM should reply with a single short phrase (or empty string).
    """
    return f"""给下面的一条记忆打一个时间语义标签（temporal_context）。

记忆内容：
---
{content}
---

要求：
- 只输出一个短语，不超过 10 个汉字
- 只在内容里有明显时间线索时给出标签；没有则输出空字符串
- 示例：工作日晚上、周末下午、月初发薪后、深夜、通勤路上、午休
- 不要输出引号、标点、解释、JSON
"""


# ---------------------------------------------------------------------------
# Pattern filtering
# ---------------------------------------------------------------------------


def filter_by_temporal_pattern(
    memories: Iterable[Memory],
    pattern: str,
) -> list[Memory]:
    """Return the subset whose ``temporal_context`` matches ``pattern``.

    Match is case-insensitive substring; empty pattern returns [].
    """
    if not pattern:
        return []
    needle = pattern.strip().lower()
    out: list[Memory] = []
    for m in memories:
        ctx = (m.temporal_context or "").lower()
        if needle in ctx:
            out.append(m)
    return out
