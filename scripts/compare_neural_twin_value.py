"""对比实验：同一个真实决策场景，有无 neural_twin 工具的 agent 回答差异。

目的：验证把 Neural-Twin 作为 agent 工具，是不是真的带来了"基于你自己历史
数据"的增量价值 —— 而不是 LLM 自己编的通用建议。
"""

from __future__ import annotations

import asyncio
import os
import sys
import time

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

from mybot.agent import Agent
from mybot.config import Config
from mybot.llm import configure_default_client
from mybot.memory import MemoryEngine
from mybot.tools import load_enabled_tools


SCENARIOS = [
    # (标题, 问题)
    ("买耳机", "我最近看中一款 3000 元的蓝牙耳机，想买，但犹豫。你觉得我该不该买？"),
]


async def build_agent(with_neural_twin: bool, config: Config, memory_engine) -> Agent:
    tools = load_enabled_tools(config, memory_engine=memory_engine)
    if not with_neural_twin:
        tools = [t for t in tools if t.name != "neural_twin"]
    agent = Agent(config=config, memory_engine=memory_engine, tools=tools)
    return agent


def _sep(title: str) -> str:
    bar = "=" * 70
    return f"\n{bar}\n{title}\n{bar}"


async def main() -> None:
    config = Config.load("config.yaml")
    configure_default_client(
        default_model=config.model.default,
        fallback_model=config.model.fallback,
        api_keys=config.api_keys,
    )

    # 用临时 DB，避免污染 launchd 跑着的 telegram bot 的记忆库
    tmp_db = f"data/memory_compare_{int(time.time())}.db"

    async def llm_call(messages):
        from mybot.llm import completion
        resp = await completion(messages=messages)
        msg = resp["choices"][0]["message"]
        content = msg.get("content") or ""
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        return content

    memory_engine = MemoryEngine(db_path=tmp_db, llm_callable=llm_call)
    await memory_engine.initialize()

    agent_without = await build_agent(False, config, memory_engine)
    agent_with = await build_agent(True, config, memory_engine)

    print(_sep("对比实验：mybot agent 对真实决策场景的回答"))
    print(f"模型：{agent_with.model}")
    print(f"A 组工具：{agent_without.list_tools()}")
    print(f"B 组工具：{agent_with.list_tools()}")

    for title, question in SCENARIOS:
        print(_sep(f"场景：{title}"))
        print(f"用户问：{question}")

        print(_sep("A 组 —— 没有 neural_twin（纯通用 LLM 建议）"))
        t0 = time.time()
        try:
            reply_a = await agent_without.chat(f"compare-a-{title}", question)
        except Exception as exc:  # noqa: BLE001
            reply_a = f"[ERROR] {exc!r}"
        print(reply_a)
        print(f"\n[耗时 {time.time()-t0:.1f}s]")

        print(_sep("B 组 —— 带 neural_twin（会调工具查你自己的历史决策网络）"))
        t0 = time.time()
        try:
            reply_b = await agent_with.chat(f"compare-b-{title}", question)
        except Exception as exc:  # noqa: BLE001
            reply_b = f"[ERROR] {exc!r}"
        print(reply_b)
        print(f"\n[耗时 {time.time()-t0:.1f}s]")

    print(_sep("测试完成"))
    print(f"临时记忆库：{tmp_db}（可删除）")


if __name__ == "__main__":
    asyncio.run(main())
