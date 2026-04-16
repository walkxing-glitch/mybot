"""工具系统基类：BaseTool 抽象类 + ToolResult 数据类。"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any


@dataclass
class ToolResult:
    """工具执行结果。"""

    success: bool
    output: str
    error: str | None = None

    def to_openai_message(self, tool_call_id: str, name: str) -> dict[str, Any]:
        """格式化为 OpenAI function-calling 协议的 tool 消息。"""
        if self.success:
            content = self.output
        else:
            content = f"[ERROR] {self.error or 'unknown error'}\n{self.output}".rstrip()
        return {
            "role": "tool",
            "tool_call_id": tool_call_id,
            "name": name,
            "content": content,
        }


class BaseTool(ABC):
    """所有工具的抽象基类。

    子类必须定义：
    - name: 工具名（LLM 用它来调用）
    - description: 工具描述
    - parameters: JSON Schema 格式的参数定义
    - async execute(**params) -> ToolResult
    """

    name: str = ""
    description: str = ""
    parameters: dict[str, Any] = {}

    @abstractmethod
    async def execute(self, **params: Any) -> ToolResult:
        """执行工具。"""
        raise NotImplementedError

    def to_openai_schema(self) -> dict[str, Any]:
        """转成 OpenAI function-calling 工具定义。"""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters
                or {"type": "object", "properties": {}, "required": []},
            },
        }

    def __repr__(self) -> str:
        return f"<Tool {self.name}>"
