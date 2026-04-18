# soho-twin-towers 拆分实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将 mybot 内嵌的 palace 模块拆为独立 HTTP 服务 `soho-twin-towers`（端口 8004），mybot 侧改为 HTTP client 调用。

**Architecture:** palace 核心代码（store, writer, retriever, chunker, router, atrium, embedder, ids, config）整体迁入 soho-twin-towers/palace/，新增 FastAPI gateway 层暴露 REST API。mybot 侧删除 palace/ 目录，新建 PalaceClient HTTP 封装，通过 memory_engine 接口无缝对接现有 Agent 和 MemoryTool。

**Tech Stack:** FastAPI + uvicorn, httpx (async client), apsw + sqlite-vec, litellm, 豆包 API embedder

**Spec:** `docs/superpowers/specs/2026-04-18-soho-twin-towers-design.md`

---

## File Structure

### soho-twin-towers/ (新项目，位于 /Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/)

```
soho-twin-towers/
├── pyproject.toml
├── config.yaml
├── CLAUDE.md
├── gateway/
│   ├── __init__.py
│   ├── main.py              # FastAPI app, startup/shutdown hooks
│   ├── deps.py              # 依赖注入：get_palace() / get_config()
│   └── routes/
│       ├── __init__.py
│       ├── session.py        # POST /session/context, POST /session/archive
│       ├── atrium.py         # GET /atrium, GET /atrium/{id}
│       ├── drawers.py        # GET /drawers/{date}, GET /drawers/{id}/raw
│       └── stats.py          # GET /stats
├── palace/
│   ├── __init__.py           # MemoryPalace 门面（从 mybot 复制）
│   ├── writer.py
│   ├── chunker.py
│   ├── router.py
│   ├── store.py
│   ├── atrium.py
│   ├── retriever.py
│   ├── config.py
│   ├── ids.py
│   ├── embedder_doubao.py
│   ├── embedder.py
│   ├── reranker.py
│   └── migrations/
│       └── 001_init.sql
├── tests/
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── tool_failure_session.json
│   │   ├── beijing_spending_session.json
│   │   └── multi_topic_session.json
│   ├── test_ids.py
│   ├── test_config.py
│   ├── test_store.py
│   ├── test_migration.py
│   ├── test_chunker.py
│   ├── test_router.py
│   ├── test_retriever.py
│   ├── test_atrium.py
│   ├── test_atrium_guards.py
│   ├── test_writer.py
│   ├── test_no_rust.py
│   ├── test_palace_facade.py
│   ├── test_reranker.py
│   ├── test_embedder.py
│   ├── test_api_session.py    # 新增：gateway API 测试
│   ├── test_api_atrium.py     # 新增
│   ├── test_api_drawers.py    # 新增
│   └── test_api_stats.py      # 新增
└── data/
    └── palace.db              # 从 mybot/data/palace.db 复制
```

### mybot 侧修改

```
mybot/
├── mybot/
│   ├── tools/
│   │   └── palace_client.py   # 新增：PalaceClient + PalaceTool HTTP 版
│   ├── gateway/
│   │   ├── cli.py             # 修改：_try_build_palace → PalaceClient
│   │   └── telegram.py        # 修改：同上
│   ├── __main__.py            # 修改：移除 memory 子命令
│   └── tools/
│       └── __init__.py        # 修改：加载新 PalaceTool
├── config.yaml                # 修改：palace 段改为 base_url
└── CLAUDE.md                  # 修改：架构描述
```

---

## Task 1: 创建 soho-twin-towers 项目骨架

**Files:**
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/pyproject.toml`
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/CLAUDE.md`

- [ ] **Step 1: 初始化 git 仓库和项目目录**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work
mkdir -p soho-twin-towers/{gateway/routes,palace/migrations,tests/fixtures,data}
cd soho-twin-towers
git init
```

- [ ] **Step 2: 创建 pyproject.toml**

```toml
[project]
name = "soho-twin-towers"
version = "0.1.0"
description = "丽泽SOHO双塔DNA记忆系统 — 独立 HTTP 记忆服务"
requires-python = ">=3.11"
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn[standard]>=0.30.0",
    "litellm>=1.40.0",
    "httpx>=0.27.0",
    "sqlite-vec>=0.1.3",
    "apsw>=3.45",
    "numpy>=1.26.0",
    "pyyaml>=6.0",
    "python-dotenv>=1.0.0",
    "jieba>=0.42.1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
    "httpx>=0.27.0",
]
local-embedder = [
    "FlagEmbedding>=1.2.0",
    "torch>=2.2.0",
]

[project.scripts]
soho-twin-towers = "gateway.main:cli_entry"

[tool.pytest.ini_options]
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
]
asyncio_mode = "auto"

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

- [ ] **Step 3: 创建 CLAUDE.md**

```markdown
# soho-twin-towers — 丽泽SOHO双塔DNA记忆系统

## 概述

独立 HTTP 记忆服务（端口 8004），为 mybot 提供对话归档、上下文检索、中庭规则管理。

## 架构

- gateway/ — FastAPI HTTP 层
- palace/ — 核心记忆逻辑（store, writer, retriever, chunker, router, atrium）
- 数据库：SQLite + sqlite-vec（apsw 驱动）
- 向量嵌入：豆包 API（DoubaoEmbedder, 2048 维）

## 技术约束

- 2017 MacBook Pro Intel i7，torch 最高 2.2.2
- 向量嵌入用豆包 API，不用本地模型
- 本机用 `docker-compose`（非 `docker compose`）

## 测试

\```bash
pytest tests/ -m "not slow" -q
pytest tests/test_no_rust.py -v
\```

## 启动

\```bash
uvicorn gateway.main:app --host 0.0.0.0 --port 8004
\```
```

- [ ] **Step 4: 创建空 __init__.py 文件**

```bash
touch gateway/__init__.py gateway/routes/__init__.py palace/__init__.py tests/__init__.py
```

- [ ] **Step 5: 创建 .gitignore**

```
__pycache__/
*.pyc
.env
data/palace.db
*.egg-info/
dist/
.pytest_cache/
```

- [ ] **Step 6: Commit**

```bash
git add -A
git commit -m "init: soho-twin-towers project skeleton"
```

---

## Task 2: 迁移 palace 核心模块（无修改复制）

**Files:**
- Copy: `mybot/mybot/palace/ids.py` → `soho-twin-towers/palace/ids.py`
- Copy: `mybot/mybot/palace/config.py` → `soho-twin-towers/palace/config.py`
- Copy: `mybot/mybot/palace/embedder_doubao.py` → `soho-twin-towers/palace/embedder_doubao.py`
- Copy: `mybot/mybot/palace/embedder.py` → `soho-twin-towers/palace/embedder.py`
- Copy: `mybot/mybot/palace/reranker.py` → `soho-twin-towers/palace/reranker.py`
- Copy: `mybot/mybot/palace/migrations/001_init.sql` → `soho-twin-towers/palace/migrations/001_init.sql`

- [ ] **Step 1: 复制无依赖的底层模块**

这些模块没有 `mybot.` 前缀的 import，可以直接复制：

```bash
SRC=/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/mybot/palace
DST=/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/palace

cp "$SRC/ids.py" "$DST/ids.py"
cp "$SRC/config.py" "$DST/config.py"
cp "$SRC/embedder_doubao.py" "$DST/embedder_doubao.py"
cp "$SRC/embedder.py" "$DST/embedder.py"
cp "$SRC/reranker.py" "$DST/reranker.py"
cp "$SRC/migrations/001_init.sql" "$DST/migrations/001_init.sql"
```

- [ ] **Step 2: 复制有内部 import 的模块，修正 import 路径**

以下模块的 import 需要从 `from mybot.palace.xxx` 改为 `from palace.xxx`：

```bash
cp "$SRC/store.py" "$DST/store.py"
cp "$SRC/chunker.py" "$DST/chunker.py"
cp "$SRC/router.py" "$DST/router.py"
cp "$SRC/retriever.py" "$DST/retriever.py"
cp "$SRC/atrium.py" "$DST/atrium.py"
cp "$SRC/writer.py" "$DST/writer.py"
cp "$SRC/__init__.py" "$DST/__init__.py"
```

然后对每个文件执行 import 替换：

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers
# 将 from mybot.palace. 替换为 from palace.
sed -i '' 's/from mybot\.palace\./from palace./g' palace/*.py
# 将 import mybot.palace 替换为 import palace
sed -i '' 's/import mybot\.palace/import palace/g' palace/*.py
```

- [ ] **Step 3: 移除 __init__.py 中的 tool_palace 和 cli 相关 import（如有）**

检查 `palace/__init__.py` 是否 import 了 `tool_palace` 或 `cli`，如有则删除（这些不迁移到 soho-twin-towers，gateway 层替代它们）。

- [ ] **Step 4: 验证 import 正确性**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers
python -c "from palace.ids import Tower, make_id, parse_id; print('ids OK')"
python -c "from palace.config import PalaceConfig, AtriumGuards; print('config OK')"
```

Expected: 两行 "OK" 输出。

- [ ] **Step 5: Commit**

```bash
git add palace/
git commit -m "feat: migrate palace core modules from mybot"
```

---

## Task 3: 迁移测试和 fixtures

**Files:**
- Copy: `mybot/tests/palace/conftest.py` → `soho-twin-towers/tests/conftest.py`
- Copy: `mybot/tests/palace/fixtures/` → `soho-twin-towers/tests/fixtures/`
- Copy: `mybot/tests/palace/test_*.py` → `soho-twin-towers/tests/`

- [ ] **Step 1: 复制 fixtures 和 conftest**

```bash
SRC=/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/tests/palace
DST=/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/tests

cp "$SRC/fixtures/tool_failure_session.json" "$DST/fixtures/"
cp "$SRC/fixtures/beijing_spending_session.json" "$DST/fixtures/"
cp "$SRC/fixtures/multi_topic_session.json" "$DST/fixtures/"
cp "$SRC/conftest.py" "$DST/conftest.py"
```

- [ ] **Step 2: 复制所有测试文件（排除 test_cli.py, test_tool_palace.py, test_agent_integration.py）**

这三个测试文件与 mybot 集成相关，不迁移：
- `test_cli.py` — palace CLI 不迁移（gateway 层替代）
- `test_tool_palace.py` — PalaceTool 是 mybot 的 BaseTool，不迁移
- `test_agent_integration.py` — 测试 mybot.palace 的 import chain

```bash
for f in test_ids test_config test_store test_migration test_chunker test_router \
         test_retriever test_atrium test_atrium_guards test_writer test_no_rust \
         test_palace_facade test_reranker test_embedder; do
    cp "$SRC/${f}.py" "$DST/${f}.py"
done
```

- [ ] **Step 3: 修正所有测试文件的 import 路径**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers
sed -i '' 's/from mybot\.palace\./from palace./g' tests/*.py
sed -i '' 's/from mybot\.palace /from palace /g' tests/*.py
sed -i '' 's/import mybot\.palace/import palace/g' tests/*.py
```

- [ ] **Step 4: 修正 conftest.py 中的 fixture 路径**

conftest.py 中 fixtures 的路径引用需要更新。原来的路径是相对 `tests/palace/fixtures/`，现在改为 `tests/fixtures/`：

检查 conftest.py 中是否有 `Path(__file__).parent / "fixtures"` 之类的路径引用，确保它在新目录结构下正确。

- [ ] **Step 5: 运行测试验证迁移正确性**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers
python -m pytest tests/test_ids.py tests/test_config.py -v
```

Expected: 全部 PASSED。

- [ ] **Step 6: 运行完整测试套件**

```bash
python -m pytest tests/ -m "not slow" -q
```

Expected: 全部 PASSED（~40+ tests）。

- [ ] **Step 7: 运行防锈回归测试**

```bash
python -m pytest tests/test_no_rust.py -v
```

Expected: 全部 PASSED。

- [ ] **Step 8: Commit**

```bash
git add tests/
git commit -m "feat: migrate palace tests from mybot"
```

---

## Task 4: 创建 config.yaml 和 LLM client

**Files:**
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/config.yaml`
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/gateway/llm.py`

- [ ] **Step 1: 创建 config.yaml**

```yaml
server:
  host: "0.0.0.0"
  port: 8004

llm:
  model: "deepseek/deepseek-chat"

embedder:
  provider: "doubao"
  dim: 2048

palace:
  enabled: true
  db_path: "data/palace.db"
  current_year_scope: 3
  embedder: "doubao"
  embedder_dim: 2048
  reranker: "none"
  top_k_south: 5
  top_k_fact: 3

rooms:
  fixed:
    1: "消费"
    2: "工作"
    3: "人际"
    4: "健康"
    5: "学习"
    6: "技术"
    7: "项目"
    8: "家庭"
    9: "出行"
    10: "情绪"
  misc_room: 20

atrium_guards:
  blacklist_patterns:
    - "不可用"
    - "未能找到"
    - "服务中断"
    - "超时"
    - "工具报错"
    - "无法访问"
    - "操作失败"
    - "连接失败"
  evidence_threshold: 3
  evidence_days_span: 2
  require_manual_approve: true
  review_cycle_days: 30
  stale_archive_days: 90
```

- [ ] **Step 2: 创建 gateway/llm.py — litellm wrapper**

```python
"""Self-contained LLM client using litellm — no dependency on mybot."""

from __future__ import annotations

import litellm


async def llm_call(messages: list[dict], *, model: str = "deepseek/deepseek-chat") -> str:
    resp = await litellm.acompletion(model=model, messages=messages)
    try:
        content = resp.choices[0].message.content or ""
        if isinstance(content, list):
            content = "".join(
                p.get("text", "") if isinstance(p, dict) else str(p) for p in content
            )
        return content
    except (KeyError, IndexError, TypeError):
        return ""
```

- [ ] **Step 3: Commit**

```bash
git add config.yaml gateway/llm.py
git commit -m "feat: add config.yaml and litellm wrapper"
```

---

## Task 5: 创建 gateway 依赖注入层

**Files:**
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/gateway/deps.py`

- [ ] **Step 1: 写 deps.py 的测试**

创建 `tests/test_deps.py`：

```python
"""Test gateway dependency injection."""

import pytest
from unittest.mock import AsyncMock, patch

from gateway.deps import build_palace, load_config


def test_load_config_reads_yaml(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg_file.write_text("palace:\n  enabled: true\n  db_path: test.db\n")
    cfg = load_config(str(cfg_file))
    assert cfg["palace"]["enabled"] is True


@pytest.mark.asyncio
async def test_build_palace_returns_initialized(tmp_path):
    cfg = {
        "palace": {
            "enabled": True,
            "db_path": str(tmp_path / "palace.db"),
            "current_year_scope": 3,
            "embedder": "doubao",
            "embedder_dim": 2048,
            "reranker": "none",
            "top_k_south": 5,
            "top_k_fact": 3,
        },
        "rooms": {"fixed": {1: "消费"}, "misc_room": 20},
        "atrium_guards": {},
        "llm": {"model": "deepseek/deepseek-chat"},
    }
    with patch("gateway.deps.DoubaoEmbedder") as MockEmb:
        import numpy as np
        MockEmb.return_value.encode.return_value = np.random.rand(1, 2048).astype("float32")
        MockEmb.return_value.dim = 2048
        palace = await build_palace(cfg)
        assert palace is not None
        await palace.close()
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_deps.py -v
```

Expected: FAIL — `gateway.deps` not found.

- [ ] **Step 3: 实现 deps.py**

```python
"""Dependency injection: build and hold the MemoryPalace singleton."""

from __future__ import annotations

from functools import lru_cache
from typing import Any

import yaml

from palace import MemoryPalace
from palace.config import PalaceConfig
from palace.embedder_doubao import DoubaoEmbedder
from gateway.llm import llm_call


_palace_instance: MemoryPalace | None = None


def load_config(path: str = "config.yaml") -> dict[str, Any]:
    with open(path) as f:
        return yaml.safe_load(f)


class _NullReranker:
    model_name = "none"
    def rerank(self, query: str, docs: list) -> list:
        return []


def _build_embedder(pcfg: PalaceConfig):
    if pcfg.embedder == "doubao":
        return DoubaoEmbedder(dim=pcfg.embedder_dim)
    from palace.embedder import Embedder
    return Embedder(model_name=pcfg.embedder, dim=pcfg.embedder_dim)


def _build_reranker(pcfg: PalaceConfig):
    if pcfg.reranker == "none":
        return _NullReranker()
    from palace.reranker import Reranker
    return Reranker(model_name=pcfg.reranker)


async def build_palace(cfg: dict[str, Any]) -> MemoryPalace:
    pcfg = PalaceConfig.from_dict(cfg)
    model = cfg.get("llm", {}).get("model", "deepseek/deepseek-chat")

    async def _llm(messages: list[dict]) -> str:
        return await llm_call(messages, model=model)

    embedder = _build_embedder(pcfg)
    reranker = _build_reranker(pcfg)
    palace = MemoryPalace(cfg=pcfg, llm=_llm, embedder=embedder, reranker=reranker)
    await palace.initialize()
    return palace


async def get_palace() -> MemoryPalace:
    global _palace_instance
    if _palace_instance is None:
        raise RuntimeError("Palace not initialized — call startup first")
    return _palace_instance


async def startup(config_path: str = "config.yaml") -> None:
    global _palace_instance
    cfg = load_config(config_path)
    _palace_instance = await build_palace(cfg)


async def shutdown() -> None:
    global _palace_instance
    if _palace_instance is not None:
        await _palace_instance.close()
        _palace_instance = None
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_deps.py -v
```

Expected: PASSED.

- [ ] **Step 5: Commit**

```bash
git add gateway/deps.py tests/test_deps.py
git commit -m "feat: gateway dependency injection layer"
```

---

## Task 6: 创建 /session/context 和 /session/archive 路由

**Files:**
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/gateway/routes/session.py`
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/tests/test_api_session.py`

- [ ] **Step 1: 写 API 测试**

```python
"""Test /session/context and /session/archive endpoints."""

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from gateway.main import app


@pytest.fixture
def mock_palace():
    palace = AsyncMock()
    palace.assemble_context.return_value = "## 中庭\n- [规则] test rule"
    palace.archive_session.return_value = AsyncMock(
        north_ids=["N-2026-108-01-01"],
        south_ids=["S-2026-108-01-01"],
        atrium_ids=["abc123"],
        merge_count=0,
    )
    return palace


@pytest.fixture
async def client(mock_palace):
    with patch("gateway.routes.session.get_palace", return_value=mock_palace):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_session_context(client, mock_palace):
    resp = await client.post("/session/context", json={"query": "北京消费"})
    assert resp.status_code == 200
    data = resp.json()
    assert "context" in data
    mock_palace.assemble_context.assert_called_once_with("北京消费")


async def test_session_context_empty_query(client):
    resp = await client.post("/session/context", json={"query": ""})
    assert resp.status_code == 422 or resp.status_code == 400


async def test_session_archive(client, mock_palace):
    resp = await client.post("/session/archive", json={
        "session_id": "tg-12345-1713420000",
        "messages": [
            {"role": "user", "content": "记住：以后别加太多emoji"},
            {"role": "assistant", "content": "好的，已记住。"},
        ],
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["session_id"] == "tg-12345-1713420000"
    assert data["north_ids"] == ["N-2026-108-01-01"]
    assert data["south_ids"] == ["S-2026-108-01-01"]
    assert data["atrium_ids"] == ["abc123"]
    assert data["merge_count"] == 0


async def test_session_archive_missing_messages(client):
    resp = await client.post("/session/archive", json={"session_id": "x"})
    assert resp.status_code == 422
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_api_session.py -v
```

Expected: FAIL — modules not found.

- [ ] **Step 3: 创建 session.py 路由**

```python
"""Session endpoints: context retrieval and archive."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from gateway.deps import get_palace

router = APIRouter(prefix="/session", tags=["session"])


class ContextRequest(BaseModel):
    query: str = Field(..., min_length=1)


class ContextResponse(BaseModel):
    context: str


class ArchiveRequest(BaseModel):
    session_id: str = Field(..., min_length=1)
    messages: list[dict[str, Any]] = Field(..., min_length=1)


class ArchiveResponse(BaseModel):
    session_id: str
    north_ids: list[str]
    south_ids: list[str]
    atrium_ids: list[str]
    merge_count: int


@router.post("/context", response_model=ContextResponse)
async def session_context(req: ContextRequest):
    palace = await get_palace()
    ctx = await palace.assemble_context(req.query)
    return ContextResponse(context=ctx)


@router.post("/archive", response_model=ArchiveResponse)
async def session_archive(req: ArchiveRequest):
    palace = await get_palace()
    result = await palace.archive_session(
        session_id=req.session_id,
        messages=req.messages,
    )
    return ArchiveResponse(
        session_id=req.session_id,
        north_ids=result.north_ids,
        south_ids=result.south_ids,
        atrium_ids=result.atrium_ids,
        merge_count=result.merge_count,
    )
```

- [ ] **Step 4: 创建 gateway/main.py（最小版本，只挂 session 路由）**

```python
"""FastAPI application for soho-twin-towers."""

from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

from gateway.deps import startup, shutdown


@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup()
    yield
    await shutdown()


app = FastAPI(title="soho-twin-towers", version="0.1.0", lifespan=lifespan)


from gateway.routes.session import router as session_router  # noqa: E402

app.include_router(session_router)


def cli_entry():
    import uvicorn
    uvicorn.run("gateway.main:app", host="0.0.0.0", port=8004, reload=True)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest tests/test_api_session.py -v
```

Expected: PASSED.

- [ ] **Step 6: Commit**

```bash
git add gateway/routes/session.py gateway/main.py tests/test_api_session.py
git commit -m "feat: /session/context and /session/archive endpoints"
```

---

## Task 7: 创建 /atrium 路由

**Files:**
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/gateway/routes/atrium.py`
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/tests/test_api_atrium.py`

- [ ] **Step 1: 写测试**

```python
"""Test /atrium endpoints."""

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from gateway.main import app


@pytest.fixture
def mock_palace():
    palace = AsyncMock()
    palace.store.list_atrium_entries.return_value = [
        {"id": "a1", "entry_type": "rule", "content": "别加emoji", "status": "active"},
    ]
    palace.store.get_atrium_entry.return_value = {
        "id": "a1", "entry_type": "rule", "content": "别加emoji", "status": "active",
    }
    return palace


@pytest.fixture
async def client(mock_palace):
    with patch("gateway.routes.atrium.get_palace", return_value=mock_palace):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_list_atrium(client, mock_palace):
    resp = await client.get("/atrium")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == "a1"


async def test_list_atrium_filter_type(client, mock_palace):
    resp = await client.get("/atrium?entry_type=rule")
    assert resp.status_code == 200
    mock_palace.store.list_atrium_entries.assert_called()


async def test_get_atrium_entry(client, mock_palace):
    resp = await client.get("/atrium/a1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "a1"


async def test_get_atrium_entry_not_found(client, mock_palace):
    mock_palace.store.get_atrium_entry.return_value = None
    resp = await client.get("/atrium/nonexistent")
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_api_atrium.py -v
```

Expected: FAIL.

- [ ] **Step 3: 实现 atrium.py 路由**

```python
"""Atrium endpoints: list and get entries."""

from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, HTTPException, Query

from gateway.deps import get_palace

router = APIRouter(prefix="/atrium", tags=["atrium"])


@router.get("")
async def list_atrium(
    status: Optional[str] = Query("active"),
    entry_type: Optional[str] = Query(None),
) -> list[dict[str, Any]]:
    palace = await get_palace()
    entries = await palace.store.list_atrium_entries(
        status=status,
        entry_type=entry_type,
    )
    return entries


@router.get("/{entry_id}")
async def get_atrium_entry(entry_id: str) -> dict[str, Any]:
    palace = await get_palace()
    entry = await palace.store.get_atrium_entry(entry_id)
    if entry is None:
        raise HTTPException(status_code=404, detail=f"Entry {entry_id} not found")
    return entry
```

- [ ] **Step 4: 在 main.py 注册路由**

在 `gateway/main.py` 中添加：

```python
from gateway.routes.atrium import router as atrium_router
app.include_router(atrium_router)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest tests/test_api_atrium.py -v
```

Expected: PASSED.

- [ ] **Step 6: Commit**

```bash
git add gateway/routes/atrium.py tests/test_api_atrium.py gateway/main.py
git commit -m "feat: /atrium list and get endpoints"
```

---

## Task 8: 创建 /drawers 路由

**Files:**
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/gateway/routes/drawers.py`
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/tests/test_api_drawers.py`

- [ ] **Step 1: 写测试**

```python
"""Test /drawers endpoints."""

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from gateway.main import app


@pytest.fixture
def mock_palace():
    palace = AsyncMock()
    palace.store.get_day_room_map.return_value = {
        1: {"room_type": "fixed", "room_label": "消费", "drawer_count": 2},
    }
    palace.store.get_north_drawer.return_value = {
        "id": "N-2026-108-01-01",
        "raw_messages": [{"role": "user", "content": "hello"}],
    }
    palace.store.get_south_drawer.return_value = {
        "id": "S-2026-108-01-01",
        "summary": "打招呼",
    }
    return palace


@pytest.fixture
async def client(mock_palace):
    with patch("gateway.routes.drawers.get_palace", return_value=mock_palace):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_list_day_drawers(client, mock_palace):
    resp = await client.get("/drawers/2026-04-18")
    assert resp.status_code == 200
    data = resp.json()
    assert "1" in data or 1 in data


async def test_get_drawer_raw_north(client, mock_palace):
    resp = await client.get("/drawers/N-2026-108-01-01/raw")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == "N-2026-108-01-01"


async def test_get_drawer_raw_south(client, mock_palace):
    resp = await client.get("/drawers/S-2026-108-01-01/raw")
    assert resp.status_code == 200


async def test_get_drawer_not_found(client, mock_palace):
    mock_palace.store.get_north_drawer.return_value = None
    resp = await client.get("/drawers/N-2026-999-01-01/raw")
    assert resp.status_code == 404
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_api_drawers.py -v
```

Expected: FAIL.

- [ ] **Step 3: 实现 drawers.py 路由**

```python
"""Drawer endpoints: list day drawers, get raw content."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException

from gateway.deps import get_palace

router = APIRouter(prefix="/drawers", tags=["drawers"])


@router.get("/{date}")
async def list_day_drawers(date: str) -> dict[str, Any]:
    palace = await get_palace()
    room_map = await palace.store.get_day_room_map(date)
    return room_map


@router.get("/{drawer_id}/raw")
async def get_drawer_raw(drawer_id: str) -> dict[str, Any]:
    palace = await get_palace()
    if drawer_id.startswith("N-"):
        result = await palace.store.get_north_drawer(drawer_id)
    elif drawer_id.startswith("S-"):
        result = await palace.store.get_south_drawer(drawer_id)
    else:
        raise HTTPException(400, f"drawer_id must start with N- or S-, got {drawer_id!r}")
    if result is None:
        raise HTTPException(404, f"Drawer {drawer_id} not found")
    return result
```

- [ ] **Step 4: 在 main.py 注册路由**

```python
from gateway.routes.drawers import router as drawers_router
app.include_router(drawers_router)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest tests/test_api_drawers.py -v
```

Expected: PASSED.

- [ ] **Step 6: Commit**

```bash
git add gateway/routes/drawers.py tests/test_api_drawers.py gateway/main.py
git commit -m "feat: /drawers list and raw endpoints"
```

---

## Task 9: 创建 /stats 路由

**Files:**
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/gateway/routes/stats.py`
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/tests/test_api_stats.py`

- [ ] **Step 1: 写测试**

```python
"""Test /stats endpoint."""

import pytest
from unittest.mock import AsyncMock, patch
from httpx import AsyncClient, ASGITransport

from gateway.main import app


@pytest.fixture
def mock_palace():
    palace = AsyncMock()
    palace.get_stats.return_value = {
        "north_drawers": 10,
        "south_drawers": 10,
        "atrium_active": 3,
        "atrium_pending": 1,
    }
    return palace


@pytest.fixture
async def client(mock_palace):
    with patch("gateway.routes.stats.get_palace", return_value=mock_palace):
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as c:
            yield c


async def test_stats(client, mock_palace):
    resp = await client.get("/stats")
    assert resp.status_code == 200
    data = resp.json()
    assert data["north_drawers"] == 10
    assert data["south_drawers"] == 10
    assert data["atrium_active"] == 3
    assert data["atrium_pending"] == 1
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_api_stats.py -v
```

Expected: FAIL.

- [ ] **Step 3: 实现 stats.py 路由**

```python
"""Stats endpoint."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from gateway.deps import get_palace

router = APIRouter(tags=["stats"])


@router.get("/stats")
async def stats() -> dict[str, Any]:
    palace = await get_palace()
    return await palace.get_stats()
```

- [ ] **Step 4: 在 main.py 注册路由**

```python
from gateway.routes.stats import router as stats_router
app.include_router(stats_router)
```

- [ ] **Step 5: 运行测试确认通过**

```bash
python -m pytest tests/test_api_stats.py -v
```

Expected: PASSED.

- [ ] **Step 6: 运行全部测试确认无回归**

```bash
python -m pytest tests/ -m "not slow" -q
```

Expected: 全部 PASSED。

- [ ] **Step 7: Commit**

```bash
git add gateway/routes/stats.py tests/test_api_stats.py gateway/main.py
git commit -m "feat: /stats endpoint"
```

---

## Task 10: 复制数据库并做端到端冒烟测试

**Files:**
- Copy: `mybot/data/palace.db` → `soho-twin-towers/data/palace.db`

- [ ] **Step 1: 复制现有数据库**

```bash
cp /Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/data/palace.db \
   /Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/data/palace.db
```

- [ ] **Step 2: 手动启动服务验证**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers
uvicorn gateway.main:app --port 8004 &
sleep 2
curl -s http://localhost:8004/stats | python -m json.tool
kill %1
```

Expected: 返回 JSON 格式的统计数据（north_drawers, south_drawers 等）。

- [ ] **Step 3: Commit（如有任何调整）**

```bash
git add -A
git commit -m "chore: smoke test passed, data migration verified"
```

---

## Task 11: mybot 侧 — 创建 PalaceClient HTTP 封装

**Files:**
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/mybot/tools/palace_client.py`
- Create: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/tests/test_palace_client.py`

- [ ] **Step 1: 写测试**

```python
"""Test PalaceClient HTTP wrapper."""

import pytest
from unittest.mock import AsyncMock, patch

from mybot.tools.palace_client import PalaceClient


@pytest.fixture
def client():
    return PalaceClient(base_url="http://localhost:8004")


async def test_get_context_for_prompt(client):
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"context": "## 中庭\n- rule"}
    mock_resp.raise_for_status = lambda: None

    with patch("mybot.tools.palace_client.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_resp)
        result = await client.get_context_for_prompt("北京消费")
        assert "中庭" in result


async def test_end_session(client):
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "session_id": "test-1",
        "north_ids": ["N-2026-108-01-01"],
        "south_ids": ["S-2026-108-01-01"],
        "atrium_ids": [],
        "merge_count": 0,
    }
    mock_resp.raise_for_status = lambda: None

    with patch("mybot.tools.palace_client.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(return_value=mock_resp)
        result = await client.end_session("test-1", [{"role": "user", "content": "hi"}])
        assert result["session_id"] == "test-1"


async def test_get_stats(client):
    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"north_drawers": 5, "south_drawers": 5, "atrium_active": 2, "atrium_pending": 0}
    mock_resp.raise_for_status = lambda: None

    with patch("mybot.tools.palace_client.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.get = AsyncMock(return_value=mock_resp)
        result = await client.get_stats()
        assert result["north_drawers"] == 5


async def test_service_down_returns_empty_context(client):
    import httpx
    with patch("mybot.tools.palace_client.httpx.AsyncClient") as MockClient:
        MockClient.return_value.__aenter__ = AsyncMock(return_value=MockClient.return_value)
        MockClient.return_value.__aexit__ = AsyncMock(return_value=False)
        MockClient.return_value.post = AsyncMock(side_effect=httpx.ConnectError("refused"))
        result = await client.get_context_for_prompt("test")
        assert result == ""
```

- [ ] **Step 2: 运行测试确认失败**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot
python -m pytest tests/test_palace_client.py -v
```

Expected: FAIL — module not found.

- [ ] **Step 3: 实现 palace_client.py**

```python
"""HTTP client for soho-twin-towers, compatible with memory_engine interface."""

from __future__ import annotations

import json
import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class PalaceClient:
    """HTTP client that wraps soho-twin-towers REST API.

    Implements the same interface as MemoryPalace (get_context_for_prompt,
    end_session, get_stats) so it can be injected as memory_engine.
    """

    def __init__(self, base_url: str = "http://localhost:8004", timeout: float = 30.0):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    async def get_context_for_prompt(self, query: str) -> str:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                resp = await c.post(f"{self.base_url}/session/context", json={"query": query})
                resp.raise_for_status()
                return resp.json().get("context", "")
        except Exception as exc:
            logger.warning("Palace context failed: %s", exc)
            return ""

    async def end_session(self, session_id: str, conversation_messages: list[dict[str, Any]]) -> dict[str, Any]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                resp = await c.post(
                    f"{self.base_url}/session/archive",
                    json={"session_id": session_id, "messages": conversation_messages},
                )
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Palace archive failed: %s", exc)
            return {"session_id": session_id, "north_ids": [], "south_ids": [], "atrium_ids": [], "merge_count": 0}

    async def get_stats(self) -> dict[str, int]:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                resp = await c.get(f"{self.base_url}/stats")
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Palace stats failed: %s", exc)
            return {}

    async def get_day_room_map(self, date: str) -> dict:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                resp = await c.get(f"{self.base_url}/drawers/{date}")
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Palace drawers failed: %s", exc)
            return {}

    async def get_atrium_entries(self, *, status: str = "active", entry_type: str | None = None) -> list[dict]:
        try:
            params: dict[str, str] = {"status": status}
            if entry_type:
                params["entry_type"] = entry_type
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                resp = await c.get(f"{self.base_url}/atrium", params=params)
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Palace atrium list failed: %s", exc)
            return []

    async def get_atrium_entry(self, entry_id: str) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                resp = await c.get(f"{self.base_url}/atrium/{entry_id}")
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Palace atrium get failed: %s", exc)
            return None

    async def get_drawer_raw(self, drawer_id: str) -> dict | None:
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as c:
                resp = await c.get(f"{self.base_url}/drawers/{drawer_id}/raw")
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
        except Exception as exc:
            logger.warning("Palace drawer raw failed: %s", exc)
            return None
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_palace_client.py -v
```

Expected: PASSED.

- [ ] **Step 5: Commit**

```bash
git add mybot/tools/palace_client.py tests/test_palace_client.py
git commit -m "feat: PalaceClient HTTP wrapper for soho-twin-towers"
```

---

## Task 12: mybot 侧 — 创建 HTTP 版 PalaceTool

**Files:**
- Modify: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/mybot/tools/palace_client.py`（追加 PalaceTool 类）

- [ ] **Step 1: 写测试**

在 `tests/test_palace_client.py` 末尾追加：

```python
from mybot.tools.palace_client import PalaceHttpTool


async def test_palace_http_tool_stats():
    mock_client = AsyncMock()
    mock_client.get_stats.return_value = {"north_drawers": 5, "south_drawers": 5, "atrium_active": 2, "atrium_pending": 0}
    tool = PalaceHttpTool(palace_client=mock_client)
    result = await tool.execute(operation="stats")
    assert result.success
    assert "north_drawers" in result.output


async def test_palace_http_tool_list_atrium():
    mock_client = AsyncMock()
    mock_client.get_atrium_entries.return_value = [{"id": "a1", "content": "rule"}]
    tool = PalaceHttpTool(palace_client=mock_client)
    result = await tool.execute(operation="list_atrium")
    assert result.success


async def test_palace_http_tool_unknown_op():
    mock_client = AsyncMock()
    tool = PalaceHttpTool(palace_client=mock_client)
    result = await tool.execute(operation="nonexistent")
    assert not result.success
```

- [ ] **Step 2: 运行测试确认失败**

```bash
python -m pytest tests/test_palace_client.py::test_palace_http_tool_stats -v
```

Expected: FAIL — PalaceHttpTool not found.

- [ ] **Step 3: 在 palace_client.py 中追加 PalaceHttpTool**

```python
from mybot.tools.base import BaseTool, ToolResult


class PalaceHttpTool(BaseTool):
    """Agent tool that calls soho-twin-towers via HTTP."""

    name = "palace"
    description = (
        "查丽泽SOHO双塔DNA记忆系统。可按坐标取原文 (get_raw_conversation)、"
        "列某天的抽屉 (list_day_drawers)、看中庭条目 (list_atrium / "
        "show_atrium_entry)、看统计 (stats)。"
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": [
                    "get_raw_conversation", "list_day_drawers",
                    "list_atrium", "show_atrium_entry", "stats",
                ],
            },
            "drawer_id": {
                "type": "string",
                "description": "N-YYYY-FFF-RR-DD 北塔 or S-YYYY-FFF-RR-DD 南塔坐标",
            },
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "entry_id": {"type": "string"},
            "entry_type": {
                "type": "string",
                "enum": ["rule", "preference", "fact"],
            },
        },
        "required": ["operation"],
    }

    def __init__(self, palace_client: PalaceClient):
        self.client = palace_client

    async def execute(self, **params: Any) -> ToolResult:
        op = params.get("operation")
        try:
            if op == "get_raw_conversation":
                drawer_id = params.get("drawer_id")
                if not drawer_id:
                    return ToolResult(success=False, output="", error="get_raw_conversation 需要 drawer_id")
                result = await self.client.get_drawer_raw(drawer_id)
                if result is None:
                    return ToolResult(success=False, output="", error=f"no drawer {drawer_id}")
                return ToolResult(success=True, output=json.dumps(result, ensure_ascii=False, default=str))

            if op == "list_day_drawers":
                date = params.get("date")
                if not date:
                    return ToolResult(success=False, output="", error="list_day_drawers 需要 date 参数")
                result = await self.client.get_day_room_map(date)
                return ToolResult(success=True, output=json.dumps(result, ensure_ascii=False))

            if op == "list_atrium":
                entries = await self.client.get_atrium_entries(
                    status="active", entry_type=params.get("entry_type"),
                )
                return ToolResult(success=True, output=json.dumps(entries, ensure_ascii=False, default=str))

            if op == "show_atrium_entry":
                eid = params.get("entry_id")
                if not eid:
                    return ToolResult(success=False, output="", error="show_atrium_entry 需要 entry_id")
                entry = await self.client.get_atrium_entry(eid)
                if entry is None:
                    return ToolResult(success=False, output="", error=f"no entry {eid}")
                return ToolResult(success=True, output=json.dumps(entry, ensure_ascii=False, default=str))

            if op == "stats":
                s = await self.client.get_stats()
                return ToolResult(success=True, output=json.dumps(s, ensure_ascii=False))

            return ToolResult(success=False, output="", error=f"unknown operation {op!r}")
        except Exception as exc:
            return ToolResult(success=False, output="", error=str(exc))
```

- [ ] **Step 4: 运行测试确认通过**

```bash
python -m pytest tests/test_palace_client.py -v
```

Expected: PASSED.

- [ ] **Step 5: Commit**

```bash
git add mybot/tools/palace_client.py tests/test_palace_client.py
git commit -m "feat: PalaceHttpTool — HTTP-based palace agent tool"
```

---

## Task 13: mybot 侧 — 修改 gateway 初始化逻辑

**Files:**
- Modify: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/mybot/gateway/cli.py:347-421`
- Modify: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/mybot/__main__.py:16-19`
- Modify: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/mybot/tools/__init__.py:68-70`

- [ ] **Step 1: 修改 _try_build_palace 为 HTTP client**

将 `mybot/gateway/cli.py` 中的 `_try_build_palace` 函数替换为：

```python
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

    base_url = palace_cfg.get("base_url", "http://localhost:8004")

    try:
        from mybot.tools.palace_client import PalaceClient
        client = PalaceClient(base_url=base_url)
        # Probe: check if soho-twin-towers is running
        stats = await client.get_stats()
        if stats:
            logger.info("PalaceClient connected to %s", base_url)
            return client
        else:
            logger.warning("soho-twin-towers at %s returned empty stats", base_url)
            return client  # still usable, might be empty DB
    except Exception as exc:  # noqa: BLE001
        logger.warning("PalaceClient init failed (%s); falling back.", exc)
        return None
```

- [ ] **Step 2: 删除不再需要的 helper 函数**

从 `cli.py` 中删除以下函数（它们直接操作 palace 内部，不再需要）：
- `_build_embedder` (lines 402-407)
- `_build_reranker` (lines 410-414)
- `_NullReranker` (lines 417-421)

- [ ] **Step 3: 修改 tools/__init__.py — 增加 PalaceHttpTool 加载**

在 `load_enabled_tools` 函数中，在 MemoryTool 之后添加 PalaceHttpTool 加载：

```python
    # Manually instantiate PalaceHttpTool if enabled and memory_engine is a PalaceClient.
    if "palace" in enabled and memory_engine is not None:
        try:
            from mybot.tools.palace_client import PalaceClient, PalaceHttpTool
            if isinstance(memory_engine, PalaceClient):
                tools.append(PalaceHttpTool(palace_client=memory_engine))
                logger.debug("Registered tool: palace (HTTP)")
        except Exception as exc:  # noqa: BLE001
            logger.warning("Failed to instantiate PalaceHttpTool: %s", exc)
```

同时将 `SKIP_MODULES` 更新为：`{"base", "memory_tool", "palace_client"}`

- [ ] **Step 4: 修改 __main__.py — 移除 memory 子命令**

删除 `__main__.py` 中的 lines 16-19（`if sys.argv[1] == "memory"` block），palace CLI 子命令不再由 mybot 提供。

- [ ] **Step 5: 运行 mybot 现有测试确保不破坏**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot
python -m pytest tests/ -m "not slow" -q --ignore=tests/palace
```

Expected: PASSED（排除 palace 目录的测试，因为即将删除）。

- [ ] **Step 6: Commit**

```bash
git add mybot/gateway/cli.py mybot/__main__.py mybot/tools/__init__.py
git commit -m "refactor: gateway init uses PalaceClient HTTP instead of embedded palace"
```

---

## Task 14: mybot 侧 — 更新 config.yaml 和 CLAUDE.md

**Files:**
- Modify: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/config.yaml`
- Modify: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/CLAUDE.md`

- [ ] **Step 1: 修改 config.yaml**

将 palace 段简化为：

```yaml
palace:
  enabled: true
  base_url: "http://localhost:8004"
```

删除 config.yaml 中的 `rooms:` 和 `atrium_guards:` 段（这些配置现在在 soho-twin-towers/config.yaml 里）。

- [ ] **Step 2: 在 tools.enabled 中添加 palace**

```yaml
tools:
  enabled:
    - shell
    - code
    - web_search
    - web_fetch
    - ontology
    - neural_twin
    - calendar
    - memory
    - palace
```

- [ ] **Step 3: 更新 CLAUDE.md**

将"内置丽泽SOHO双塔DNA记忆系统"改为"通过 HTTP 调用丽泽SOHO双塔DNA记忆系统"，增加 soho-twin-towers 引用：

```markdown
## 记忆系统接口

MyBot 的记忆通过 HTTP 调用 soho-twin-towers（本机 :8004）。

- Base URL: `http://localhost:8004`
- API: /session/context, /session/archive, /atrium, /drawers, /stats
- 项目位置: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/`
```

更新跨项目原则：
```markdown
- mybot ↔ myontology / neural-twin / soho-twin-towers **永远只 HTTP 调用，不改对方代码**
```

更新测试段：
```markdown
## 测试

\```bash
pytest tests/ -m "not slow" -q
\```
```

- [ ] **Step 4: Commit**

```bash
git add config.yaml CLAUDE.md
git commit -m "docs: update config and CLAUDE.md for soho-twin-towers HTTP integration"
```

---

## Task 15: mybot 侧 — 删除 palace/ 和 tests/palace/

**Files:**
- Delete: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/mybot/palace/` (entire directory)
- Delete: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/tests/palace/` (entire directory)

- [ ] **Step 1: 确认 soho-twin-towers 测试全部通过**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers
python -m pytest tests/ -m "not slow" -q
```

Expected: 全部 PASSED。

- [ ] **Step 2: 确认 mybot 测试（排除 palace）通过**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot
python -m pytest tests/ -m "not slow" -q --ignore=tests/palace
```

Expected: 全部 PASSED。

- [ ] **Step 3: 删除 mybot/palace/ 目录**

```bash
rm -rf /Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/mybot/palace
```

- [ ] **Step 4: 删除 tests/palace/ 目录**

```bash
rm -rf /Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/tests/palace
```

- [ ] **Step 5: 从 pyproject.toml 移除 palace 专属依赖**

从 `pyproject.toml` 的 `dependencies` 中移除（这些现在在 soho-twin-towers 的依赖中）：
- `"FlagEmbedding>=1.2.0"` — 本地 embedder 备用方案
- `"torch>=2.2.0"` — 本地 embedder 的依赖
- `"jieba>=0.42.1"` — FTS 分词

保留 `sqlite-vec`, `apsw`, `numpy` 如果 mybot 其他地方还在用，否则也可移除。检查其他模块是否依赖这些包。

- [ ] **Step 6: 验证 mybot 全部测试通过**

```bash
python -m pytest tests/ -m "not slow" -q
```

Expected: 全部 PASSED（无 palace 测试了）。

- [ ] **Step 7: Commit**

```bash
git add -A
git commit -m "refactor: remove embedded palace module, now served by soho-twin-towers"
```

---

## Task 16: 端到端验证

- [ ] **Step 1: 启动 soho-twin-towers**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers
uvicorn gateway.main:app --port 8004 &
```

- [ ] **Step 2: 验证 API 可用**

```bash
# Stats
curl -s http://localhost:8004/stats | python -m json.tool

# Context
curl -s -X POST http://localhost:8004/session/context \
  -H "Content-Type: application/json" \
  -d '{"query": "北京消费"}' | python -m json.tool

# Atrium
curl -s http://localhost:8004/atrium | python -m json.tool
```

- [ ] **Step 3: 通过 mybot CLI 验证集成**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot
python -m mybot cli --session test-integration
```

输入一条消息，验证记忆上下文正常注入、对话正常归档。

- [ ] **Step 4: 通过 mybot Telegram 验证（可选）**

```bash
python -m mybot telegram
```

发一条消息，检查 palace 日志是否显示 archive 成功。

- [ ] **Step 5: 防锈实测 — 发送含工具失败的对话，确认不进中庭**

在 CLI 或 Telegram 中模拟一次工具失败场景，检查 /atrium 接口确认没有新增错误类条目。

- [ ] **Step 6: 最终 commit（如有调整）**

```bash
git add -A
git commit -m "test: end-to-end integration verified"
```

---

## 执行顺序总结

| 阶段 | Tasks | 项目 | 依赖 |
|------|-------|------|------|
| 1. 新建项目 | 1 | soho-twin-towers | 无 |
| 2. 迁移核心 | 2, 3 | soho-twin-towers | Task 1 |
| 3. HTTP 层 | 4, 5, 6, 7, 8, 9 | soho-twin-towers | Task 2-3 |
| 4. 冒烟测试 | 10 | soho-twin-towers | Task 9 |
| 5. mybot 改造 | 11, 12, 13, 14 | mybot | Task 10 |
| 6. 清理删除 | 15 | mybot | Task 13-14 |
| 7. E2E 验证 | 16 | 两个项目 | Task 15 |

Tasks 6-9（四个路由）可以并行执行。Tasks 11-12 可以并行执行。
