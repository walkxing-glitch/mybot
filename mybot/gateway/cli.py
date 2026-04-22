"""CLI Gateway：rich 渲染的交互式 REPL。

两种入口：
    # 1) 用已构造好的 Agent 直接跑（spec 里的签名）
    await run_cli(agent)

    # 2) 从 config 文件加载 → 构造 Agent → 跑 REPL
    await run_cli_from_config(config_path="config.yaml")
"""

from __future__ import annotations

import asyncio
import logging
import signal
from typing import TYPE_CHECKING, Any

from rich.console import Console
from rich.markdown import Markdown
from rich.panel import Panel
from rich.prompt import Prompt
from rich.table import Table
from rich.text import Text

if TYPE_CHECKING:  # pragma: no cover
    from mybot.agent import Agent

logger = logging.getLogger(__name__)


WELCOME_BANNER = """[bold cyan]MyBot[/bold cyan]  — 邢智强的个人 AI 助手

输入你的消息开始对话。内置命令：
  [green]/help[/green]         查看命令列表
  [green]/model[/green] <name> 切换底层模型
  [green]/memory[/green]       查看记忆统计
  [green]/reset[/green]        清空当前会话历史
  [green]/tools[/green]        列出已加载的工具
  [green]/quit[/green]         退出（也可用 Ctrl+D）

[dim]Ctrl+C 中断当前请求；Ctrl+D 退出。[/dim]
"""


HELP_TEXT = """
[bold]可用命令[/bold]

  /help                   显示本帮助
  /model <name>           切换模型，例如 /model deepseek-chat
  /memory                 打印记忆引擎统计（若已接入）
  /reset                  清空当前会话历史
  /tools                  列出工具
  /quit, /exit            退出 REPL
"""


DEFAULT_SESSION_ID = "cli"


async def run_cli(
    agent: "Agent",
    *,
    session_id: str = DEFAULT_SESSION_ID,
    console: Console | None = None,
) -> None:
    """主入口：REPL 直到用户退出。独占 stdin/stdout。"""
    console = console or Console()

    _print_banner(console, agent)

    loop = asyncio.get_running_loop()
    current_task: asyncio.Task[str] | None = None

    def handle_sigint() -> None:
        # Ctrl+C：只中断当前请求；如果没有请求在跑，打印提示不退出
        nonlocal current_task
        if current_task and not current_task.done():
            current_task.cancel()
        else:
            console.print(
                "\n[dim](Ctrl+C 再按一次不会退出；用 /quit 或 Ctrl+D 退出)[/dim]"
            )

    sigint_bound = False
    try:
        loop.add_signal_handler(signal.SIGINT, handle_sigint)
        sigint_bound = True
    except (NotImplementedError, RuntimeError):
        sigint_bound = False

    try:
        while True:
            try:
                user_input = await _read_input(console)
            except EOFError:
                # Ctrl+D
                console.print("\n[dim]再见。[/dim]")
                break
            except KeyboardInterrupt:
                if not sigint_bound:
                    handle_sigint()
                continue

            if user_input is None:
                continue
            user_input = user_input.strip()
            if not user_input:
                continue

            # 内置命令
            if user_input.startswith("/"):
                should_exit = await _handle_command(
                    user_input, agent, console, session_id
                )
                if should_exit:
                    break
                continue

            # 调 agent，支持中途取消
            current_task = asyncio.create_task(agent.chat(session_id, user_input))
            try:
                with console.status("[cyan]思考中…[/cyan]", spinner="dots"):
                    reply = await current_task
            except asyncio.CancelledError:
                console.print("[yellow]请求已取消。[/yellow]")
                current_task = None
                continue
            except Exception as exc:  # noqa: BLE001
                logger.exception("agent.chat raised")
                console.print(f"[red]错误[/red]：{exc}")
                current_task = None
                continue
            current_task = None

            _render_reply(console, reply)

    finally:
        if sigint_bound:
            try:
                loop.remove_signal_handler(signal.SIGINT)
            except (NotImplementedError, RuntimeError):
                pass


# ----------------------------------------------------------------------
# 命令分发
# ----------------------------------------------------------------------


async def _handle_command(
    line: str,
    agent: "Agent",
    console: Console,
    session_id: str,
) -> bool:
    """返回 True 表示应该退出 REPL。"""
    parts = line.split(maxsplit=1)
    cmd = parts[0].lower()
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("/quit", "/exit"):
        console.print("[dim]再见。[/dim]")
        return True

    if cmd == "/help":
        console.print(HELP_TEXT)
        return False

    if cmd == "/model":
        if not arg:
            current = getattr(agent, "model", "?")
            console.print(f"当前模型：[bold]{current}[/bold]")
        else:
            agent.set_model(arg)
            console.print(f"[green]已切换模型 →[/green] [bold]{arg}[/bold]")
        return False

    if cmd == "/memory":
        await _print_memory_stats(agent, console)
        return False

    if cmd == "/tools":
        _print_tools(agent, console)
        return False

    if cmd == "/reset":
        agent.reset_session(session_id)
        console.print("[green]会话历史已清空。[/green]")
        return False

    console.print(f"[red]未知命令[/red]：{cmd}。输入 /help 查看列表。")
    return False


async def _print_memory_stats(agent: "Agent", console: Console) -> None:
    engine = getattr(agent, "memory_engine", None)
    if engine is None:
        console.print("[dim]（未接入记忆引擎）[/dim]")
        return
    stats_fn = getattr(engine, "get_stats", None)
    if stats_fn is None:
        console.print("[dim]（记忆引擎未实现 get_stats）[/dim]")
        return
    try:
        result = stats_fn()
        if asyncio.iscoroutine(result):
            result = await result
    except Exception as exc:  # noqa: BLE001
        console.print(f"[red]获取记忆统计失败[/red]：{exc}")
        return

    if isinstance(result, dict):
        table = Table(title="记忆统计", show_edge=True)
        table.add_column("指标", style="cyan")
        table.add_column("值", style="bold")
        for k, v in result.items():
            table.add_row(str(k), str(v))
        console.print(table)
    else:
        console.print(result)


def _print_tools(agent: "Agent", console: Console) -> None:
    tools = getattr(agent, "tools", None) or []
    if not tools:
        console.print("[dim]（当前未加载工具）[/dim]")
        return
    table = Table(title="已加载工具", show_edge=True)
    table.add_column("工具名", style="cyan", no_wrap=True)
    table.add_column("描述")
    for t in tools:
        desc = (t.description or "").strip().splitlines()[0] if t.description else ""
        table.add_row(t.name, desc)
    console.print(table)


# ----------------------------------------------------------------------
# 输入/输出
# ----------------------------------------------------------------------


async def _read_input(console: Console) -> str | None:
    """在 executor 里跑 input()，避免阻塞事件循环。"""
    loop = asyncio.get_running_loop()

    def _prompt() -> str:
        return Prompt.ask("[bold magenta]你[/bold magenta]", console=console)

    return await loop.run_in_executor(None, _prompt)


def _render_reply(console: Console, reply: str) -> None:
    text = (reply or "").strip()
    if not text:
        console.print("[dim]（空回复）[/dim]")
        return
    try:
        console.print(
            Panel(
                Markdown(text),
                title="[bold cyan]MyBot[/bold cyan]",
                border_style="cyan",
            )
        )
    except Exception:  # noqa: BLE001 — markdown parse 异常时退化成纯文本
        console.print(
            Panel(
                Text(text),
                title="[bold cyan]MyBot[/bold cyan]",
                border_style="cyan",
            )
        )


def _print_banner(console: Console, agent: "Agent") -> None:
    model = getattr(agent, "model", "?")
    tool_names = agent.list_tools() if hasattr(agent, "list_tools") else []
    tools_line = ", ".join(tool_names) if tool_names else "（无）"

    console.print(Panel.fit(WELCOME_BANNER.strip(), border_style="cyan"))
    console.print(
        f"[dim]模型[/dim] [bold]{model}[/bold]   [dim]工具[/dim] {tools_line}\n"
    )


# ----------------------------------------------------------------------
# 从 config 启动的便捷入口（被 __main__.py 调用）
# ----------------------------------------------------------------------


async def run_cli_from_config(
    *,
    config_path: str = "config.yaml",
    session_id: str = DEFAULT_SESSION_ID,
) -> None:
    """加载 config + 构造 Agent + 跑 REPL。"""
    from mybot.agent import Agent
    from mybot.config import Config
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

    configure_default_client(
        default_model=config.model.default,
        fallback_model=config.model.fallback,
        api_keys=config.api_keys,
    )

    # Build memory engine FIRST so MemoryTool can receive the injection.
    # Preference order: MemoryPalace (new) → MemoryEngine (legacy).
    memory_engine = await _try_build_palace(config)
    if memory_engine is None and MemoryEngine is not None:
        try:
            memory_engine = _build_memory_engine(config, MemoryEngine)
            if memory_engine is not None:
                init = getattr(memory_engine, "initialize", None)
                if callable(init):
                    maybe = init()
                    if asyncio.iscoroutine(maybe):
                        await maybe
                logger.info("using legacy MemoryEngine")
        except Exception as exc:  # noqa: BLE001
            logger.warning("初始化记忆引擎失败: %s", exc)
            memory_engine = None

    tools: list[Any] = []
    if load_enabled_tools is not None:
        try:
            tools = load_enabled_tools(config, memory_engine=memory_engine)  # type: ignore[misc]
        except Exception as exc:  # noqa: BLE001
            logger.warning("加载工具失败: %s", exc)

    # Evolution queue for chat_event logging (no heartbeat in CLI — short-lived)
    evolution_queue = None
    if hasattr(config, "heartbeat") and config.heartbeat.enabled:
        from mybot.evolution.queue import EvolutionQueue
        evolution_queue = EvolutionQueue()
        await evolution_queue.initialize()

    agent = Agent(config=config, memory_engine=memory_engine, tools=tools, evolution_queue=evolution_queue)
    try:
        await run_cli(agent, session_id=session_id)
    finally:
        try:
            await agent.close()
        except Exception:  # noqa: BLE001
            logger.exception("agent.close failed")


async def _try_build_palace(config: Any) -> Any:
    """Try to construct PalaceClient per config.palace settings.

    Returns None if palace is disabled — caller should fall back to MemoryEngine.
    """
    cfg_dict = getattr(config, "raw", None) or _config_as_dict(config)
    if not cfg_dict:
        return None

    palace_cfg = cfg_dict.get("palace", {})
    if not palace_cfg.get("enabled", False):
        return None

    try:
        from mybot.tools.palace_client import DEFAULT_BASE_URL, PalaceClient
        base_url = palace_cfg.get("base_url") or DEFAULT_BASE_URL
        client = PalaceClient(base_url=base_url)
        stats = await client.get_stats()
        if stats:
            logger.info("PalaceClient connected to %s", base_url)
        else:
            logger.info("PalaceClient connected to %s (empty db)", base_url)
        return client
    except Exception as exc:  # noqa: BLE001
        logger.warning("PalaceClient init failed (%s); falling back.", exc)
        return None


def _config_as_dict(config: Any) -> dict[str, Any]:
    """Extract config as dict — support dataclass-like and plain dict."""
    if isinstance(config, dict):
        return config
    # Try to re-read YAML; Config.load strips unknown keys.
    try:
        import yaml  # type: ignore
        from pathlib import Path
        p = Path("config.yaml")
        if p.exists():
            return yaml.safe_load(p.read_text()) or {}
    except Exception:  # noqa: BLE001
        pass
    return {}


def _build_memory_engine(config: Any, MemoryEngineCls: Any) -> Any:
    """尝试用已知的两种签名构造 MemoryEngine。失败返回 None。"""
    import inspect

    # 供 MemoryEngine 用的简单 LLM callable：接收 messages，返回文本
    from mybot.llm import completion

    async def llm_call(messages: list[dict[str, Any]]) -> str:
        resp = await completion(messages=messages)
        try:
            msg = resp["choices"][0]["message"]
            content = msg.get("content") or ""
            if isinstance(content, list):
                content = "".join(
                    p.get("text", "") if isinstance(p, dict) else str(p)
                    for p in content
                )
            return content
        except (KeyError, IndexError, TypeError):
            return ""

    # 尝试 (db_path, llm_callable) 签名
    try:
        sig = inspect.signature(MemoryEngineCls)
        params = sig.parameters
        if "db_path" in params and "llm_callable" in params:
            db_path = "data/memory.db"
            return MemoryEngineCls(db_path=db_path, llm_callable=llm_call)
        if "config" in params:
            return MemoryEngineCls(config=config)
    except (TypeError, ValueError):
        pass

    # 兜底：无参
    try:
        return MemoryEngineCls()
    except TypeError:
        return None


if __name__ == "__main__":  # pragma: no cover
    asyncio.run(run_cli_from_config())
