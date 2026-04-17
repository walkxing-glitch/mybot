"""端到端：让 agent 真的综合调用新的 ontology 认知层端点。"""

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
from mybot.llm import configure_default_client, completion
from mybot.memory import MemoryEngine
from mybot.tools import load_enabled_tools


async def llm_call(messages):
    resp = await completion(messages=messages)
    msg = resp["choices"][0]["message"]
    c = msg.get("content") or ""
    if isinstance(c, list):
        c = "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)
    return c


async def main() -> None:
    config = Config.load("config.yaml")
    configure_default_client(
        default_model=config.model.default,
        fallback_model=config.model.fallback,
        api_keys=config.api_keys,
    )
    tmp_db = f"data/memory_e2e_{int(time.time())}.db"
    mem = MemoryEngine(db_path=tmp_db, llm_callable=llm_call)
    await mem.initialize()

    tools = load_enabled_tools(config, memory_engine=mem)
    agent = Agent(config=config, memory_engine=mem, tools=tools)
    print(f"tools = {agent.list_tools()}")
    print()

    # 综合问题：综合 place + people + wechat
    q = "我 2020 年 4 月去过哪些地方？那个月我和谁微信聊天最多？"
    print("=" * 70)
    print(f"问：{q}")
    t0 = time.time()
    try:
        ans = await agent.chat("e2e-month", q)
    except Exception as exc:
        ans = f"[ERROR] {exc!r}"
    print(f"答（{time.time()-t0:.1f}s）：")
    print(ans)

    try:
        os.remove(tmp_db)
    except OSError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
