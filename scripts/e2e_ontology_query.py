"""端到端：让 agent 真的去调 ontology 工具回答一个关于本体论的问题。"""

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

    # 真实场景 —— 会驱动 LLM 调 ontology 工具
    questions = [
        "在我的本体论里，查一下'时洋'这个人是谁？和我有什么关系？",
        "我本体论知识图谱里目前有哪些类型的对象？各多少个？",
    ]

    for q in questions:
        print("=" * 70)
        print(f"问：{q}")
        t0 = time.time()
        try:
            ans = await agent.chat(f"e2e-{hash(q)}", q)
        except Exception as exc:
            ans = f"[ERROR] {exc!r}"
        print(f"答（{time.time()-t0:.1f}s）：")
        print(ans)
        print()

    # 清 tmp
    try:
        os.remove(tmp_db)
    except OSError:
        pass


if __name__ == "__main__":
    asyncio.run(main())
