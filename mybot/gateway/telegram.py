"""Telegram Gateway：python-telegram-bot v20+ 长轮询模式。

两种入口：
    # 1) 用已构造好的 Agent + token 直接跑（spec 里的签名）
    await run_telegram(agent, token="123:abc...")

    # 2) 从 config 文件加载 → 构造 Agent → 启动 bot
    await run_telegram_from_config(config_path="config.yaml")
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Any

from telegram import Update
from telegram.constants import ChatAction
from telegram.error import TelegramError
from telegram.ext import (
    Application,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

if TYPE_CHECKING:  # pragma: no cover
    from mybot.agent import Agent

logger = logging.getLogger(__name__)


WELCOME_TEXT = (
    "你好，我是 MyBot —— 邢智强的个人 AI 助手。\n\n"
    "直接发消息就能对话。\n"
    "可用命令：\n"
    "  /start  显示欢迎语\n"
    "  /help   使用说明\n"
    "  /reset  清空当前会话历史\n"
)


HELP_TEXT = (
    "使用说明：\n"
    "• 直接输入文本即可开始对话。\n"
    "• 长任务我会先回「处理中…」，完成后再发最终结果。\n"
    "• /reset 会清空当前 chat 的会话上下文。\n"
)


AGENT_KEY = "mybot.agent"


def _session_id_for(update: Update) -> str:
    """以 chat_id 作为 session_id（每个对话框一条历史）。"""
    chat = update.effective_chat
    return str(chat.id) if chat else "tg-unknown"


async def _cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(WELCOME_TEXT)


async def _cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.effective_message is None:
        return
    await update.effective_message.reply_text(HELP_TEXT)


async def _cmd_reset(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    agent = context.application.bot_data.get(AGENT_KEY)
    if update.effective_message is None or agent is None:
        return
    sid = _session_id_for(update)
    agent.reset_session(sid)
    await update.effective_message.reply_text("会话历史已清空。")


async def _on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """处理普通文本消息。"""
    message = update.effective_message
    chat = update.effective_chat
    if message is None or chat is None:
        return
    text = (message.text or "").strip()
    if not text:
        return

    agent: "Agent" | None = context.application.bot_data.get(AGENT_KEY)
    if agent is None:
        await message.reply_text("（内部错误：agent 未初始化）")
        return

    session_id = _session_id_for(update)

    # 先发个占位消息，再启动任务。长对话完成后我们编辑占位消息；
    # 如果回复太长或编辑失败，会 fallback 到新开一条消息（能触发通知）。
    try:
        await context.bot.send_chat_action(chat_id=chat.id, action=ChatAction.TYPING)
    except TelegramError as exc:
        logger.debug("send_chat_action failed: %s", exc)

    placeholder = None
    try:
        placeholder = await message.reply_text("处理中…")
    except TelegramError as exc:
        logger.warning("failed to send placeholder: %s", exc)

    # 周期性地重发 typing action，给用户持续的处理中提示
    async def keep_typing() -> None:
        try:
            while True:
                await asyncio.sleep(4.5)
                try:
                    await context.bot.send_chat_action(
                        chat_id=chat.id, action=ChatAction.TYPING
                    )
                except TelegramError:
                    return
        except asyncio.CancelledError:
            return

    typing_task = asyncio.create_task(keep_typing())

    try:
        reply = await agent.chat(session_id=session_id, message=text)
    except Exception as exc:  # noqa: BLE001
        logger.exception("agent.chat failed")
        reply = f"抱歉，我刚才崩了一下：{exc}"
    finally:
        typing_task.cancel()
        try:
            await typing_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass

    reply = (reply or "").strip() or "（空回复）"

    await _deliver_reply(message, placeholder, reply)


async def _deliver_reply(
    original_message: Any,
    placeholder: Any,
    reply: str,
) -> None:
    """把 agent 回复投递给用户。

    策略：
    - 回复较短（≤ 3800 字符）且有占位消息：直接编辑占位消息。
      注意：编辑不触发推送通知——所以我们额外再发一条空提示的空消息？
      不行，会冗余。实际做法：长任务仍以发新消息为主，占位删除。
    - 回复超长：切块发送（每块 ≤ 3800）。
    - 任何 Telegram 异常：退化为 original_message.reply_text 纯文本新消息。
    """
    CHUNK = 3800
    chunks = _chunk_text(reply, CHUNK)

    # 长任务完成——优先发新消息（会触发通知）。
    # 但如果占位还在，先删掉它避免刷屏。
    if placeholder is not None:
        try:
            await placeholder.delete()
        except TelegramError as exc:
            logger.debug("placeholder delete failed: %s", exc)

    for chunk in chunks:
        try:
            await original_message.reply_text(chunk)
        except TelegramError as exc:
            logger.warning("reply_text failed: %s", exc)
            # 最后的挣扎：不格式化，直接纯文本
            try:
                await original_message.reply_text(chunk[:4000])
            except TelegramError:
                logger.error("final reply attempt failed, giving up chunk")
                return


def _chunk_text(text: str, size: int) -> list[str]:
    if not text:
        return [""]
    if len(text) <= size:
        return [text]

    chunks: list[str] = []
    remaining = text
    while len(remaining) > size:
        # 尽量在换行处切
        cut = remaining.rfind("\n", 0, size)
        if cut < size // 2:
            cut = size
        chunks.append(remaining[:cut])
        remaining = remaining[cut:].lstrip("\n")
    if remaining:
        chunks.append(remaining)
    return chunks


async def _on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.exception("telegram handler error: %s", context.error)
    # 尝试给用户发个友好的错误提示
    if isinstance(update, Update) and update.effective_message is not None:
        try:
            await update.effective_message.reply_text(
                "我这边出了点小问题，请稍后再试一次。"
            )
        except TelegramError:
            pass


# ----------------------------------------------------------------------
# 对外入口
# ----------------------------------------------------------------------


async def run_telegram(
    agent: "Agent",
    token: str,
    *,
    drop_pending: bool = True,
) -> None:
    """启动 Telegram bot（长轮询），阻塞直到 Ctrl+C。"""
    if not token or token.startswith("${"):
        raise ValueError(
            "Telegram bot token 缺失。请在 .env 或 config.yaml 里设置 TELEGRAM_BOT_TOKEN。"
        )

    application = Application.builder().token(token).build()
    application.bot_data[AGENT_KEY] = agent

    application.add_handler(CommandHandler("start", _cmd_start))
    application.add_handler(CommandHandler("help", _cmd_help))
    application.add_handler(CommandHandler("reset", _cmd_reset))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _on_text))
    application.add_error_handler(_on_error)

    logger.info("Telegram bot starting (long-polling)…")

    # 按 PTB v20 文档：手动控制 initialize/start/updater/shutdown，
    # 这样能和外部 event loop 协同（比 application.run_polling() 更友好）。
    await application.initialize()
    await application.start()
    updater = application.updater
    if updater is None:
        raise RuntimeError("telegram Application 没有 updater，无法长轮询。")
    await updater.start_polling(drop_pending_updates=drop_pending)

    try:
        # 无限阻塞直到被取消
        stop_event = asyncio.Event()
        await stop_event.wait()
    except (KeyboardInterrupt, asyncio.CancelledError):
        logger.info("Telegram bot stopping…")
    finally:
        try:
            await updater.stop()
        except Exception:  # noqa: BLE001
            logger.exception("updater.stop failed")
        try:
            await application.stop()
        except Exception:  # noqa: BLE001
            logger.exception("application.stop failed")
        try:
            await application.shutdown()
        except Exception:  # noqa: BLE001
            logger.exception("application.shutdown failed")


async def run_telegram_from_config(
    *,
    config_path: str = "config.yaml",
) -> None:
    """加载 config + 构造 Agent + 启动 Telegram bot。"""
    from mybot.agent import Agent
    from mybot.config import Config
    from mybot.gateway.cli import _build_memory_engine  # 复用记忆引擎构造逻辑
    from mybot.llm import configure_default_client

    try:
        from mybot.tools import load_enabled_tools  # type: ignore[attr-defined]
    except ImportError:
        load_enabled_tools = None  # type: ignore[assignment]

    try:
        from mybot.memory import MemoryEngine  # type: ignore
    except ImportError:
        MemoryEngine = None  # type: ignore[assignment]

    config = Config.load(config_path)

    token = config.gateway.telegram.token
    if not token or token.startswith("${"):
        raise ValueError(
            "Telegram bot token 未设置。请在 .env 里配置 TELEGRAM_BOT_TOKEN。"
        )

    configure_default_client(
        default_model=config.model.default,
        fallback_model=config.model.fallback,
        api_keys=config.api_keys,
    )

    tools: list[Any] = []
    if load_enabled_tools is not None:
        try:
            tools = load_enabled_tools(config)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            logger.warning("加载工具失败: %s", exc)

    memory_engine = None
    if MemoryEngine is not None:
        try:
            memory_engine = _build_memory_engine(config, MemoryEngine)
            if memory_engine is not None:
                init = getattr(memory_engine, "initialize", None)
                if callable(init):
                    maybe = init()
                    if asyncio.iscoroutine(maybe):
                        await maybe
        except Exception as exc:  # noqa: BLE001
            logger.warning("初始化记忆引擎失败: %s", exc)
            memory_engine = None

    agent = Agent(config=config, memory_engine=memory_engine, tools=tools)
    await run_telegram(agent, token=token)


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run_telegram_from_config())
