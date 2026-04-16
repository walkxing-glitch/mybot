"""LiteLLM 封装：多模型、function-calling、失败 fallback。"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any

import litellm

logger = logging.getLogger(__name__)


@dataclass
class ToolCall:
    """LLM 请求调用的工具。"""

    id: str
    name: str
    arguments: dict[str, Any]


@dataclass
class LLMResponse:
    """LLM 调用的结构化结果。content 和 tool_calls 可能同时存在。"""

    content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    model: str = ""
    usage: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


class LLMClient:
    """LiteLLM 统一封装，支持 fallback。"""

    def __init__(
        self,
        default_model: str,
        fallback_model: str | None = None,
        *,
        max_retries: int = 2,
        retry_delay: float = 1.0,
        temperature: float = 0.7,
        api_keys: dict[str, str] | None = None,
    ) -> None:
        self.default_model = default_model
        self.fallback_model = fallback_model
        self.max_retries = max_retries
        self.retry_delay = retry_delay
        self.temperature = temperature
        self.api_keys = api_keys or {}

    async def complete(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        model: str | None = None,
        temperature: float | None = None,
        **kwargs: Any,
    ) -> LLMResponse:
        """发起一次 completion，带 fallback。"""
        primary = model or self.default_model
        models_to_try: list[str] = [primary]
        if self.fallback_model and self.fallback_model != primary:
            models_to_try.append(self.fallback_model)

        last_error: Exception | None = None
        for target in models_to_try:
            for attempt in range(self.max_retries):
                try:
                    return await self._call_once(
                        model=target,
                        messages=messages,
                        tools=tools,
                        temperature=(
                            temperature if temperature is not None else self.temperature
                        ),
                        **kwargs,
                    )
                except Exception as exc:  # noqa: BLE001
                    last_error = exc
                    logger.warning(
                        "LLM call failed (model=%s, attempt=%d/%d): %s",
                        target,
                        attempt + 1,
                        self.max_retries,
                        exc,
                    )
                    if attempt + 1 < self.max_retries:
                        await asyncio.sleep(self.retry_delay * (attempt + 1))
            logger.error("Model %s exhausted retries, trying fallback.", target)

        raise RuntimeError(
            f"LLM call failed on all models ({models_to_try}): {last_error}"
        ) from last_error

    async def _call_once(
        self,
        *,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        temperature: float,
        **kwargs: Any,
    ) -> LLMResponse:
        call_kwargs: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
        }
        if tools:
            call_kwargs["tools"] = tools
            call_kwargs["tool_choice"] = kwargs.pop("tool_choice", "auto")
        call_kwargs.update(kwargs)

        resp = await litellm.acompletion(**call_kwargs)
        return self._parse_response(resp, model=model)

    @staticmethod
    def _parse_response(resp: Any, *, model: str) -> LLMResponse:
        """把 LiteLLM 的 ModelResponse 转成我们的 LLMResponse。"""
        choice = resp.choices[0]
        msg = choice.message

        content = getattr(msg, "content", None)

        tool_calls: list[ToolCall] = []
        raw_tool_calls = getattr(msg, "tool_calls", None) or []
        for tc in raw_tool_calls:
            fn = getattr(tc, "function", None)
            if fn is None:
                continue
            name = getattr(fn, "name", "") or ""
            raw_args = getattr(fn, "arguments", "") or "{}"
            if isinstance(raw_args, str):
                import json

                try:
                    args = json.loads(raw_args) if raw_args else {}
                except json.JSONDecodeError:
                    logger.warning("Bad tool args JSON: %s", raw_args)
                    args = {"__raw__": raw_args}
            else:
                args = dict(raw_args)
            tool_calls.append(
                ToolCall(id=getattr(tc, "id", "") or "", name=name, arguments=args)
            )

        usage_obj = getattr(resp, "usage", None)
        usage: dict[str, Any] = {}
        if usage_obj is not None:
            for key in ("prompt_tokens", "completion_tokens", "total_tokens"):
                val = getattr(usage_obj, key, None)
                if val is not None:
                    usage[key] = val

        return LLMResponse(
            content=content,
            tool_calls=tool_calls,
            model=model,
            usage=usage,
            raw=resp,
        )


# ----------------------------------------------------------------------
# 模块级函数入口（给 Agent 层用）
# ----------------------------------------------------------------------
#
# 约定的极简接口：
#     resp = await completion(messages, tools=..., model=...) -> dict
# 返回 OpenAI chat-completion 风格的 dict。


_default_client: LLMClient | None = None


def configure_default_client(
    *,
    default_model: str,
    fallback_model: str | None = None,
    api_keys: dict[str, str] | None = None,
    temperature: float = 0.7,
) -> LLMClient:
    """设置模块级默认 LLMClient。应用启动时调用一次。"""
    global _default_client
    _default_client = LLMClient(
        default_model=default_model,
        fallback_model=fallback_model,
        api_keys=api_keys,
        temperature=temperature,
    )
    return _default_client


def get_default_client() -> LLMClient:
    """按需创建兜底 client（未显式 configure 时用）。"""
    global _default_client
    if _default_client is None:
        _default_client = LLMClient(default_model="claude-sonnet-4-20250514")
    return _default_client


async def completion(
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
    model: str | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """模块级 completion 入口，Agent 统一调用。

    返回 OpenAI chat-completion 风格 dict：
        {"choices": [{"message": {...}}], "usage": {...}, "model": "..."}
    """
    client = get_default_client()
    resp = await client.complete(messages=messages, tools=tools, model=model, **kwargs)

    # 优先返回 litellm 原始 response 的 dict 形态，保留最多信息
    raw = resp.raw
    if raw is not None:
        if isinstance(raw, dict):
            return raw
        model_dump = getattr(raw, "model_dump", None)
        if callable(model_dump):
            try:
                return model_dump()
            except Exception:  # noqa: BLE001
                pass
        to_dict = getattr(raw, "to_dict", None)
        if callable(to_dict):
            try:
                return to_dict()
            except Exception:  # noqa: BLE001
                pass

    # 兜底：用 LLMResponse 字段手工组装
    message: dict[str, Any] = {"role": "assistant", "content": resp.content}
    if resp.tool_calls:
        import json as _json

        message["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.name,
                    "arguments": _json.dumps(tc.arguments, ensure_ascii=False),
                },
            }
            for tc in resp.tool_calls
        ]
    return {
        "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
        "model": resp.model,
        "usage": resp.usage,
    }
