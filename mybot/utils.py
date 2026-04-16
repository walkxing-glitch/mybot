"""通用工具函数：ID 生成、时间戳、token 估算。"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone


def generate_id(prefix: str | None = None) -> str:
    """生成一个 UUID4；可加前缀。"""
    raw = uuid.uuid4().hex
    return f"{prefix}_{raw}" if prefix else raw


def utc_now() -> datetime:
    """当前 UTC 时间（带 tz）。"""
    return datetime.now(timezone.utc)


def utc_now_iso() -> str:
    """ISO 8601 UTC 字符串。"""
    return utc_now().isoformat()


def parse_iso(value: str) -> datetime:
    """解析 ISO 8601 字符串为 aware datetime。"""
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def days_between(earlier: datetime, later: datetime | None = None) -> float:
    """两个时间差，返回天数（浮点）。later 默认 now。"""
    if later is None:
        later = utc_now()
    if earlier.tzinfo is None:
        earlier = earlier.replace(tzinfo=timezone.utc)
    if later.tzinfo is None:
        later = later.replace(tzinfo=timezone.utc)
    delta = later - earlier
    return delta.total_seconds() / 86400.0


def estimate_tokens(text: str) -> int:
    """极粗略 token 估算：英文按 4 字符 / token，中文按 1 字符 / token。

    不准但不依赖 tokenizer，无外部调用开销。精确计数用 litellm.token_counter。
    """
    if not text:
        return 0
    cjk = sum(1 for ch in text if "\u4e00" <= ch <= "\u9fff")
    non_cjk_chars = len(text) - cjk
    return cjk + max(1, non_cjk_chars // 4)


def estimate_messages_tokens(messages: list[dict]) -> int:
    """估算整个 messages 列表的 token 数（含 role / content 粗算）。"""
    total = 0
    for msg in messages:
        content = msg.get("content")
        if isinstance(content, str):
            total += estimate_tokens(content)
        elif isinstance(content, list):
            for part in content:
                if isinstance(part, dict):
                    total += estimate_tokens(str(part.get("text", "")))
        total += 4  # role + framing overhead
    return total
