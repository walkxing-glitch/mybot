"""工具注册表：自动发现 mybot/tools/ 下的 BaseTool 子类并注册。"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from typing import Any, Iterable

from mybot.tools.base import BaseTool, ToolResult

logger = logging.getLogger(__name__)


class ToolRegistry:
    """工具注册中心。启动时扫描 mybot.tools 子模块，实例化所有 BaseTool 子类。"""

    def __init__(self) -> None:
        self._tools: dict[str, BaseTool] = {}

    def register(self, tool: BaseTool) -> None:
        if not tool.name:
            raise ValueError(f"Tool {tool!r} missing .name")
        if tool.name in self._tools:
            logger.warning("Tool %s already registered, overwriting.", tool.name)
        self._tools[tool.name] = tool

    def get_tool(self, name: str) -> BaseTool | None:
        return self._tools.get(name)

    def list_tools(self) -> list[BaseTool]:
        return list(self._tools.values())

    def get_openai_tools_schema(
        self, enabled: Iterable[str] | None = None
    ) -> list[dict[str, Any]]:
        """返回 OpenAI function-calling 协议的 tools 数组。

        enabled 指定了则只返回这些工具；None 表示全部注册的。
        """
        if enabled is None:
            tools = self.list_tools()
        else:
            wanted = set(enabled)
            tools = [t for t in self._tools.values() if t.name in wanted]
        return [t.to_openai_schema() for t in tools]

    def auto_discover(
        self,
        package: str = "mybot.tools",
        enabled: Iterable[str] | None = None,
    ) -> None:
        """扫描 package 下所有子模块，注册其 BaseTool 子类实例。

        enabled 指定了则只加载这些工具名。
        """
        wanted = set(enabled) if enabled is not None else None
        try:
            pkg = importlib.import_module(package)
        except ImportError as exc:
            logger.error("Cannot import tool package %s: %s", package, exc)
            return

        if not hasattr(pkg, "__path__"):
            return

        # memory_tool requires dependency injection (memory_engine), handled separately
        # by load_enabled_tools — skip it in auto-discovery.
        SKIP_MODULES = {"base", "memory_tool"}

        for info in pkgutil.iter_modules(pkg.__path__):
            if info.name.startswith("_") or info.name in SKIP_MODULES:
                continue
            mod_name = f"{package}.{info.name}"
            try:
                module = importlib.import_module(mod_name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Failed to import tool module %s: %s", mod_name, exc)
                continue

            for _attr_name, obj in inspect.getmembers(module):
                if (
                    inspect.isclass(obj)
                    and issubclass(obj, BaseTool)
                    and obj is not BaseTool
                    and not inspect.isabstract(obj)
                ):
                    try:
                        instance = obj()
                    except Exception as exc:  # noqa: BLE001
                        logger.warning(
                            "Failed to instantiate %s: %s", obj.__name__, exc
                        )
                        continue
                    if wanted is not None and instance.name not in wanted:
                        continue
                    self.register(instance)
                    logger.debug("Registered tool: %s (%s)", instance.name, mod_name)


def load_enabled_tools(config: Any, memory_engine: Any = None) -> list[BaseTool]:
    """Bootstrap: auto-discover tools per config.tools.enabled, inject memory_engine for MemoryTool.

    Returns a list of BaseTool instances, ready to be passed into Agent.
    """
    enabled = set(getattr(config.tools, "enabled", []) or [])

    registry = ToolRegistry()
    # auto_discover skips memory_tool's MemoryTool (it needs an engine), we handle that manually.
    registry.auto_discover(package="mybot.tools", enabled=enabled)

    tools: list[BaseTool] = list(registry.list_tools())

    # Manually instantiate MemoryTool if enabled and memory_engine is available.
    if "memory" in enabled and memory_engine is not None:
        try:
            from mybot.tools.memory_tool import MemoryTool

            tools.append(MemoryTool(memory_engine=memory_engine))
            logger.debug("Registered tool: memory (with memory_engine)")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to instantiate MemoryTool: %s", exc)

    return tools


__all__ = ["BaseTool", "ToolResult", "ToolRegistry", "load_enabled_tools"]
