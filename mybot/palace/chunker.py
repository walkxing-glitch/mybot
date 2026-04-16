"""Chunker: LLM-driven conversation splitter + summariser."""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List


logger = logging.getLogger(__name__)
LLMCallable = Callable[[list], Awaitable[str]]


@dataclass
class Chunk:
    msg_indices: List[int]
    drawer_topic: str
    summary: str
    keywords: List[str]
    proposed_room_label: str


CHUNK_PROMPT = """你是一个对话切分与摘要代理。任务：
1. 把下面这一段对话切成若干"子话题 chunk"，每个 chunk 是连续的消息范围
2. 每个 chunk 给一个简短的 drawer_topic（≤15 字）
3. 每个 chunk 输出 summary（≤200 字，客观第三人称陈述）
4. 抽取 3-8 个 keywords
5. 给出 proposed_room_label，从以下 10 个固定类别里选最接近的；都不合适就自拟一个简短中文标签（≤4 字）：
   消费 / 工作 / 人际 / 健康 / 学习 / 技术 / 项目 / 家庭 / 出行 / 情绪

对话内容（每条消息前面是它的全局索引）：
---
{convo}
---

严格输出一个 JSON 数组，每个元素：
{{
  "msg_indices": [0, 1],
  "drawer_topic": "...",
  "summary": "...",
  "keywords": ["..."],
  "proposed_room_label": "..."
}}
不要 markdown 代码块。没有可归档内容则输出 []。
"""


class Chunker:
    def __init__(self, llm: LLMCallable):
        self.llm = llm

    async def chunk_and_summarise(self, messages: List[dict]) -> List[Chunk]:
        if not messages:
            return []
        rendered = "\n".join(
            f"[{i}] {m.get('role','user')}: {str(m.get('content',''))[:500]}"
            for i, m in enumerate(messages)
        )
        prompt = CHUNK_PROMPT.format(convo=rendered)
        try:
            raw = await self.llm([{"role": "user", "content": prompt}])
        except Exception as exc:
            logger.warning("chunker LLM failed: %s", exc)
            return []
        items = _parse_json_array(raw)
        out: List[Chunk] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                out.append(Chunk(
                    msg_indices=[int(i) for i in it.get("msg_indices", [])],
                    drawer_topic=str(it.get("drawer_topic", "未命名")).strip()[:40]
                    or "未命名",
                    summary=str(it.get("summary", "")).strip()[:300],
                    keywords=[str(k).strip() for k in (it.get("keywords") or [])][:10],
                    proposed_room_label=str(
                        it.get("proposed_room_label", "杂项")
                    ).strip()[:10] or "杂项",
                ))
            except Exception as exc:
                logger.warning("chunker item parse failed: %s", exc)
        return out


def _parse_json_array(raw: str) -> List[Any]:
    """Tolerant JSON array extraction (handles ```json fences and prefix text)."""
    text = (raw or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, list) else []
    except json.JSONDecodeError:
        pass
    start = text.find("[")
    if start < 0:
        return []
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "[":
            depth += 1
        elif text[i] == "]":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(text[start:i + 1])
                    return obj if isinstance(obj, list) else []
                except json.JSONDecodeError:
                    return []
    return []
