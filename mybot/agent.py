"""Agent 主循环：记忆检索 → 组装消息 → LLM → 工具循环 → 输出。

所有 gateway（CLI / Telegram / 未来的 HTTP）统一调用 `Agent.chat()`。
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any, TYPE_CHECKING

from mybot.llm import completion
from mybot.tools.base import BaseTool, ToolResult

if TYPE_CHECKING:  # pragma: no cover
    from mybot.config import Config
    from mybot.memory import MemoryEngine


logger = logging.getLogger(__name__)

# 工具循环最大轮数，超过强制终止避免死循环
MAX_TOOL_ITERATIONS = 10

# 每个 session 保留的最大消息条数（不含 system prompt）
MAX_HISTORY_MESSAGES = 40

# 单个工具执行超时（秒），防止卡死
TOOL_TIMEOUT_SECONDS = 60


@dataclass
class SessionState:
    """单个会话的状态：消息历史 + 元数据。"""

    session_id: str
    messages: list[dict[str, Any]] = field(default_factory=list)
    created_at: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    turn_count: int = 0

    def append(self, message: dict[str, Any]) -> None:
        self.messages.append(message)
        self.last_active = time.time()

    def trim(self, max_len: int = MAX_HISTORY_MESSAGES) -> None:
        """超过阈值时裁剪旧消息，保留最近 max_len 条。

        注意：tool_calls 和对应的 tool 消息必须成对保留，否则 LLM 会报错。
        简单起见，从截断点往后推直到遇到 user 消息，保证不会腰斩一组 tool_calls。
        """
        if len(self.messages) <= max_len:
            return

        drop = len(self.messages) - max_len
        cut = drop
        while cut < len(self.messages) and self.messages[cut].get("role") != "user":
            cut += 1
        self.messages = self.messages[cut:]


class Agent:
    """MyBot Agent 核心。

    职责：
    - 维护 session_id → 消息历史映射
    - 检索记忆、组装 prompt
    - 调用 LLM、执行工具、循环直至得到最终回复
    - 异步触发记忆整理
    """

    def __init__(
        self,
        config: "Config | dict[str, Any] | None",
        memory_engine: "MemoryEngine | None",
        tools: list[BaseTool] | None = None,
        *,
        model: str | None = None,
    ) -> None:
        self.config = config
        self.memory_engine = memory_engine
        self.tools: list[BaseTool] = list(tools or [])
        self._tool_by_name: dict[str, BaseTool] = {t.name: t for t in self.tools}
        self._sessions: dict[str, SessionState] = {}
        self._session_lock: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

        if model is not None:
            self.model = model
        else:
            self.model = self._resolve_default_model(config)

    # ------------------------------------------------------------------
    # 公开接口
    # ------------------------------------------------------------------

    async def chat(self, session_id: str, message: str) -> str:
        """主入口：收用户一条消息，返回 agent 最终回复文本。

        单个 session 内串行处理，避免并发工具调用把 messages 搞乱。
        """
        if not message or not message.strip():
            return "（空消息，忽略）"

        async with self._session_lock[session_id]:
            session = self._get_or_create_session(session_id)
            session.turn_count += 1

            # 1) 检索记忆 + 画像
            memory_context = await self._get_memory_context(message)

            # 2) 组装 system prompt（每轮重建，确保记忆最新）
            system_prompt = self._build_system_prompt(memory_context)

            # 3) 追加用户消息
            session.append({"role": "user", "content": message})

            # 4) 工具循环
            try:
                final_text = await self._run_tool_loop(session, system_prompt)
            except Exception as exc:  # noqa: BLE001
                logger.exception("agent tool loop failed: %s", exc)
                final_text = f"抱歉，处理你的请求时出错了：{exc}"
                session.append({"role": "assistant", "content": final_text})

            # 5) 裁剪历史
            session.trim()

            # 6) 异步触发记忆整理（不阻塞返回）
            if self.memory_engine is not None:
                asyncio.create_task(
                    self._post_process_memory(session_id, message, final_text)
                )

            return final_text

    def set_model(self, model: str) -> None:
        """CLI 的 /model 命令用。"""
        self.model = model

    def get_session(self, session_id: str) -> SessionState | None:
        return self._sessions.get(session_id)

    def reset_session(self, session_id: str) -> None:
        self._sessions.pop(session_id, None)

    def list_tools(self) -> list[str]:
        return [t.name for t in self.tools]

    async def close_session(self, session_id: str) -> None:
        """显式关闭会话：触发记忆引擎 end_session，并释放状态。"""
        session = self._sessions.pop(session_id, None)
        if session is None or self.memory_engine is None:
            return
        end_session = getattr(self.memory_engine, "end_session", None)
        if end_session is None:
            return
        try:
            maybe = end_session(session_id=session_id, conversation_messages=session.messages)
            if asyncio.iscoroutine(maybe):
                await maybe
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory.end_session failed: %s", exc)

    # ------------------------------------------------------------------
    # System prompt
    # ------------------------------------------------------------------

    def get_system_prompt(self) -> str:
        """基础系统 prompt，不含动态记忆注入。"""
        tool_lines = []
        for t in self.tools:
            desc = (t.description or "").strip().splitlines()[0] if t.description else ""
            tool_lines.append(f"- `{t.name}`：{desc}")
        tool_block = "\n".join(tool_lines) if tool_lines else "- （当前未加载任何工具）"

        return (
            "你是 MyBot，邢智强的个人 AI 助手。\n"
            "你有以下能力：\n"
            f"{tool_block}\n\n"
            "工作准则：\n"
            "1. 直接、简洁、不糊弄。结果是什么就说什么，不夸大、不假装完成。\n"
            "2. 必要时主动调用工具；能一次解决就别拆多轮。\n"
            "3. 默认用中文回答，除非用户明确用其他语言提问。\n"
            "4. 涉及历史事实、偏好、人物关系时，优先参考下方「记忆上下文」里的信息；\n"
            "   若当前对话内容与历史记忆冲突，主动指出差异并请用户确认。\n"
            "5. 调用 shell/code 等有副作用的工具时，先告知意图再执行危险操作。\n"
        )

    def _build_system_prompt(self, memory_context: str) -> str:
        base = self.get_system_prompt()
        if not memory_context:
            return base
        return f"{base}\n\n---\n记忆上下文\n---\n{memory_context}\n"

    # ------------------------------------------------------------------
    # 核心循环
    # ------------------------------------------------------------------

    async def _run_tool_loop(
        self,
        session: SessionState,
        system_prompt: str,
    ) -> str:
        """跑 LLM ↔ 工具循环，返回最终 assistant 文本。"""
        tools_schema = self._build_tools_schema()

        for iteration in range(MAX_TOOL_ITERATIONS):
            messages = self._assemble_messages(session, system_prompt)
            logger.debug(
                "llm call: session=%s iter=%d messages=%d tools=%d",
                session.session_id,
                iteration,
                len(messages),
                len(tools_schema),
            )

            response = await completion(
                messages=messages,
                tools=tools_schema or None,
                model=self.model,
            )

            assistant_message = self._extract_assistant_message(response)
            # 完整的 assistant 消息（含可能的 tool_calls）存到会话历史
            session.append(assistant_message)

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                content = assistant_message.get("content") or ""
                if isinstance(content, list):
                    # OpenAI 风格 multi-part，拼成纯文本
                    content = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in content
                    )
                return (content or "").strip() or "（空回复）"

            # 并行执行工具
            await self._dispatch_tool_calls(session, tool_calls)

        logger.warning(
            "tool loop hit MAX_TOOL_ITERATIONS=%d for session=%s",
            MAX_TOOL_ITERATIONS,
            session.session_id,
        )
        fallback = "（工具调用超过上限，我暂停了后续自动执行。请告诉我接下来怎么做。）"
        session.append({"role": "assistant", "content": fallback})
        return fallback

    async def _dispatch_tool_calls(
        self,
        session: SessionState,
        tool_calls: list[dict[str, Any]],
    ) -> None:
        """并行跑所有 tool_calls，把结果作为 tool 消息追加到 session。"""

        async def run_one(call: dict[str, Any]) -> dict[str, Any]:
            call_id = call.get("id") or ""
            fn = call.get("function") or {}
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            try:
                if isinstance(raw_args, str):
                    args = json.loads(raw_args) if raw_args.strip() else {}
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    args = {}
            except json.JSONDecodeError as exc:
                return {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": f"[tool_error] 工具参数 JSON 解析失败: {exc}；原始: {raw_args!r}",
                }

            output = await self._execute_tool(name, args)
            return {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": output,
            }

        results = await asyncio.gather(*(run_one(c) for c in tool_calls))
        for r in results:
            session.append(r)

    async def _execute_tool(self, name: str, arguments: dict[str, Any]) -> str:
        """查找工具并执行。所有异常吞掉转成错误字符串，不让 agent 循环挂掉。"""
        tool = self._tool_by_name.get(name)
        if tool is None:
            return (
                f"[tool_error] 未知工具 `{name}`。可用工具: "
                f"{', '.join(self._tool_by_name) or '（无）'}"
            )

        try:
            coro = tool.execute(**arguments)
            result = await asyncio.wait_for(coro, timeout=TOOL_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            return f"[tool_error] 工具 `{name}` 执行超时（>{TOOL_TIMEOUT_SECONDS}s）"
        except TypeError as exc:
            return f"[tool_error] 工具 `{name}` 参数错误: {exc}"
        except Exception as exc:  # noqa: BLE001
            logger.exception("tool %s raised", name)
            return f"[tool_error] 工具 `{name}` 执行异常: {exc}"

        # 兼容 ToolResult 或直接返回字符串
        if isinstance(result, ToolResult):
            if result.success:
                return result.output or ""
            err = result.error or result.output or "未知错误"
            return f"[tool_error] {err}"
        if isinstance(result, str):
            return result
        try:
            return json.dumps(result, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            return str(result)

    # ------------------------------------------------------------------
    # 辅助
    # ------------------------------------------------------------------

    def _get_or_create_session(self, session_id: str) -> SessionState:
        session = self._sessions.get(session_id)
        if session is None:
            session = SessionState(session_id=session_id)
            self._sessions[session_id] = session
        return session

    def _assemble_messages(
        self,
        session: SessionState,
        system_prompt: str,
    ) -> list[dict[str, Any]]:
        return [{"role": "system", "content": system_prompt}, *session.messages]

    def _build_tools_schema(self) -> list[dict[str, Any]]:
        """转成 OpenAI function-calling 格式。"""
        schema: list[dict[str, Any]] = []
        for t in self.tools:
            # 优先用 BaseTool.to_openai_schema，保持一处事实源
            to_schema = getattr(t, "to_openai_schema", None)
            if callable(to_schema):
                try:
                    schema.append(to_schema())
                    continue
                except Exception:  # noqa: BLE001
                    pass
            params = t.parameters or {"type": "object", "properties": {}}
            if "type" not in params:
                params = {"type": "object", **params}
            schema.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or "",
                        "parameters": params,
                    },
                }
            )
        return schema

    def _extract_assistant_message(self, response: Any) -> dict[str, Any]:
        """把 LLM 响应标准化成 `{role, content, tool_calls?}`。

        支持 dict（OpenAI chat completion / litellm model_dump），也兼容带属性的对象。
        """
        # 1) 直接就是 assistant dict
        if isinstance(response, dict) and response.get("role") == "assistant":
            return self._normalize_assistant_dict(response)

        # 2) OpenAI 风格 {choices: [{message: {...}}]}
        if isinstance(response, dict) and "choices" in response:
            try:
                msg = response["choices"][0]["message"]
            except (KeyError, IndexError, TypeError):
                msg = {}
            if isinstance(msg, dict):
                return self._normalize_assistant_dict(msg)

        # 3) 对象带 .choices[0].message
        choices = getattr(response, "choices", None)
        if choices:
            try:
                msg = choices[0].message
            except (AttributeError, IndexError):
                msg = None
            if msg is not None:
                normalized: dict[str, Any] = {
                    "role": "assistant",
                    "content": getattr(msg, "content", None),
                }
                tc = getattr(msg, "tool_calls", None)
                if tc:
                    normalized["tool_calls"] = [
                        {
                            "id": getattr(c, "id", "") or "",
                            "type": getattr(c, "type", "function") or "function",
                            "function": {
                                "name": getattr(getattr(c, "function", None), "name", ""),
                                "arguments": getattr(
                                    getattr(c, "function", None), "arguments", "{}"
                                ),
                            },
                        }
                        for c in tc
                    ]
                return normalized

        # 4) 兜底
        return {"role": "assistant", "content": str(response)}

    @staticmethod
    def _normalize_assistant_dict(msg: dict[str, Any]) -> dict[str, Any]:
        out: dict[str, Any] = {"role": "assistant", "content": msg.get("content")}
        tc = msg.get("tool_calls")
        if tc:
            # 统一 tool_calls 里 function.arguments 成字符串（OpenAI 协议要求字符串）
            normalized_tc = []
            for c in tc:
                if not isinstance(c, dict):
                    continue
                fn = c.get("function") or {}
                args = fn.get("arguments", "{}")
                if isinstance(args, dict):
                    args = json.dumps(args, ensure_ascii=False)
                normalized_tc.append(
                    {
                        "id": c.get("id", "") or "",
                        "type": c.get("type", "function") or "function",
                        "function": {
                            "name": fn.get("name", ""),
                            "arguments": args if isinstance(args, str) else str(args),
                        },
                    }
                )
            if normalized_tc:
                out["tool_calls"] = normalized_tc
        return out

    async def _get_memory_context(self, message: str) -> str:
        """问记忆引擎拿本轮的画像摘要 + 相关记忆拼成的 prompt 片段。"""
        if self.memory_engine is None:
            return ""
        getter = getattr(self.memory_engine, "get_context_for_prompt", None)
        if getter is None:
            return ""
        try:
            result = getter(message)
            if asyncio.iscoroutine(result):
                result = await result
            return str(result or "").strip()
        except Exception as exc:  # noqa: BLE001
            logger.warning("memory context retrieval failed: %s", exc)
            return ""

    async def _post_process_memory(
        self,
        session_id: str,
        user_message: str,
        assistant_reply: str,
    ) -> None:
        """异步：本轮对话结束后，把 messages 喂给记忆引擎做整理。"""
        engine = self.memory_engine
        if engine is None:
            return
        # 尽量走正式接口 end_session（engine 会做摘要 / 画像 / 记忆提取）
        end_session = getattr(engine, "end_session", None)
        if callable(end_session):
            session = self._sessions.get(session_id)
            if session is None:
                return
            # 给 engine 一份本轮最新的消息快照
            snapshot = list(session.messages)
            try:
                maybe = end_session(session_id=session_id, conversation_messages=snapshot)
                if asyncio.iscoroutine(maybe):
                    await maybe
                return
            except Exception as exc:  # noqa: BLE001
                logger.warning("memory.end_session failed: %s", exc)

        # 降级：直接写 noteworthy
        remember = getattr(engine, "remember", None)
        if callable(remember):
            try:
                maybe = remember(
                    content=f"user: {user_message}\nassistant: {assistant_reply}",
                    memory_type="episode",
                    session_id=session_id,
                )
                if asyncio.iscoroutine(maybe):
                    await maybe
            except Exception as exc:  # noqa: BLE001
                logger.warning("memory.remember fallback failed: %s", exc)

    # ------------------------------------------------------------------
    # Config helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_default_model(config: Any) -> str:
        """从 Config dataclass 或 dict 里取默认模型名。"""
        default = "claude-sonnet-4-20250514"
        if config is None:
            return default
        model_attr = getattr(config, "model", None)
        if model_attr is not None and hasattr(model_attr, "default"):
            return model_attr.default or default
        if isinstance(config, dict):
            m = config.get("model")
            if isinstance(m, dict):
                return m.get("default", default) or default
            if isinstance(m, str):
                return m or default
        return default
