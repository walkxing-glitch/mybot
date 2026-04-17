"""烟雾测试：新版 ontology 工具能不能对上 myontology/ontology-engine。"""

from __future__ import annotations

import asyncio
import os
import sys

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
os.chdir(REPO)

from mybot.tools.ontology import OntologyTool


async def main() -> None:
    tool = OntologyTool()
    print(f"base_url    = {tool.base_url}")
    print(f"ontology_id = {tool.ontology_id}")
    print()

    cases = [
        ("get_stats", {}),
        ("search", {"query": "邢智强", "limit": 3}),
        ("find_person", {"name": "邢智强"}),
        ("list_relators", {"limit": 2}),
        ("get_overview", {}),
    ]

    for op, extra in cases:
        print("=" * 70)
        print(f"op={op}  extra={extra}")
        res = await tool.execute(operation=op, **extra)
        if not res.success:
            print(f"[FAIL] {res.error}")
        else:
            txt = res.output
            print(f"[OK] len={len(txt)} bytes")
            print(txt[:500])
        print()


if __name__ == "__main__":
    asyncio.run(main())
