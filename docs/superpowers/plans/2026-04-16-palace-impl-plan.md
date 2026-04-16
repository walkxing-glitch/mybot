# MyBot 丽泽园记忆系统实施计划

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 把 `mybot/memory/` 的旧 MemoryEngine 替换为 `mybot/palace/` 丽泽园（南北塔 + 中庭 + 年份堆叠），彻底杜绝"失败叙述被 LLM 抽取成事实"的污染路径，引入语义检索。

**Architecture:** 新建独立 `mybot/palace/` 模块 + 独立 `data/palace.db`；旧 `data/memory.db` 保留备份不读。Agent 层把 `self.memory_engine` 替换成 `self.palace`（方法签名兼容 `get_context_for_prompt` / `end_session`）。数据流单向：对话 → 北塔 → 南塔（向量+FTS）→ 中庭（三道闸）。

**Tech Stack:** Python 3.11, aiosqlite, sqlite-vec（vec0 扩展）, FlagEmbedding（bge-m3 / bge-reranker-v2-m3）, jieba（中文分词 for FTS5）, existing LLM callable (DeepSeek/ litellm)

**Spec reference:** `docs/superpowers/specs/2026-04-16-mybot-memory-palace-design.md`

**Time estimate:** 今晚 v0.1（Phase 0-7 + 10 + 关键测试）可上线；Phase 8/9 长尾任务（nightly daemon、weekly Telegram 推送、iCloud 归档）标记为 v0.2。

---

## 全局约定

- 所有文件路径相对于 `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/`
- 所有 commit 消息前缀 `palace:`；一个 Task 一个 commit（除非明说合并）
- 每个 Task 包含 TDD 四步：写测 → 跑测确认失败 → 实现 → 跑测确认过 → commit
- 测试框架：pytest + pytest-asyncio。若没装，Task 0.1 会装上
- 库名用 `aiosqlite` 异步接口保持一致
- LLM 调用复用 `mybot.llm` 里现有的 callable（DeepSeek）
- 模块间禁止循环 import；依赖方向 `store → embedder → retriever → writer → palace → agent`
- 所有坐标 ID 格式见 spec §3.4

---

## 文件结构

```
mybot/palace/
├── __init__.py              # 公开 MemoryPalace
├── store.py                 # SQLite 层：CRUD + 事务
├── embedder.py              # bge-m3 加载与 encode
├── reranker.py              # bge-reranker-v2-m3
├── chunker.py               # LLM 切子话题 + 摘要
├── router.py                # 房间路由 + 抽屉溢出合并
├── writer.py                # archive_session 编排
├── retriever.py             # 混合检索 (BM25 + vec + RRF + rerank)
├── atrium.py                # 中庭 CRUD + 三道闸 + 注入
├── inspector.py             # 30 天巡检 [v0.2]
├── proposer.py              # inferred 候选 nightly [v0.2]
├── cli.py                   # memory review/list/... 子命令
├── tool_palace.py           # BaseTool: palace 工具
├── ids.py                   # 坐标 ID 编解码
├── config.py                # PalaceConfig dataclass
└── migrations/
    └── 001_init.sql         # schema + 触发器

tests/palace/                # pytest 测试
├── conftest.py              # fixtures: tmp_palace, fake_llm, fake_embedder
├── test_store.py
├── test_embedder.py
├── test_chunker.py
├── test_router.py
├── test_writer.py
├── test_retriever.py
├── test_atrium.py
├── test_cli.py
├── test_tool_palace.py
├── test_no_rust.py          # 防铁锈专项 E2E
└── fixtures/
    ├── beijing_spending_session.json
    ├── tool_failure_session.json
    └── multi_topic_session.json
```

`agent.py` 只改两处（见 Task 5.2）。`config.yaml` 合入 palace 段（Task 0.3）。

---

## Phase 0 — Setup & Skeleton

### Task 0.1: 安装依赖 + pytest

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: 增加依赖到 pyproject.toml**

在 `dependencies` 数组追加：
```toml
    "sqlite-vec>=0.1.3",
    "FlagEmbedding>=1.2.0",
    "jieba>=0.42.1",
    "torch>=2.2.0",
```

增加 optional dev dependencies 段：
```toml
[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.23",
]
```

- [ ] **Step 2: 安装**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot
pip install -e '.[dev]'
```

Expected: `Successfully installed` 无 error。

- [ ] **Step 3: 验证 sqlite-vec 可加载**

```bash
python -c "import sqlite_vec; import sqlite3; c=sqlite3.connect(':memory:'); c.enable_load_extension(True); sqlite_vec.load(c); print(c.execute('select vec_version()').fetchone())"
```
Expected: 打印版本号，不 raise。

- [ ] **Step 4: 验证 pytest + asyncio 可跑**

```bash
mkdir -p tests/palace && echo 'import pytest\n@pytest.mark.asyncio\nasync def test_sanity(): assert True' > tests/palace/test_sanity.py
python -m pytest tests/palace/test_sanity.py -v
```
Expected: 1 passed。

- [ ] **Step 5: Commit**

```bash
git add pyproject.toml tests/palace/test_sanity.py
git commit -m "palace: add sqlite-vec/FlagEmbedding/jieba/pytest deps"
```

---

### Task 0.2: Migration SQL

**Files:**
- Create: `mybot/palace/migrations/001_init.sql`
- Create: `mybot/palace/__init__.py` (占位，只 `__version__ = "0.1.0"`)

- [ ] **Step 1: 写 migration**

Create `mybot/palace/migrations/001_init.sql` 完整抄 spec §3.2 的所有 DDL：
- `north_drawer` + index
- `south_drawer` + 2 indices
- `south_vec` (vec0 virtual)
- `south_fts` (fts5 virtual)
- `atrium_entry` + 2 indices
- `atrium_vec` (vec0 virtual)
- `atrium_changelog`
- `atrium_blacklist_guard` 触发器（8 个 LIKE 模式）
- `drawer_merge_log`
- `day_room_map`

开头加：
```sql
-- Migration 001: initial palace schema
-- Run against an empty palace.db

PRAGMA foreign_keys = ON;
PRAGMA journal_mode = WAL;
```

- [ ] **Step 2: 写个最小测试验证 SQL 无语法错**

Create `tests/palace/test_migration.py`:
```python
import sqlite3
import sqlite_vec
from pathlib import Path

def test_migration_001_runs_clean(tmp_path):
    db = tmp_path / "palace.db"
    conn = sqlite3.connect(db)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    sql = Path("mybot/palace/migrations/001_init.sql").read_text()
    conn.executescript(sql)
    # sanity: 列出 tables
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
    )}
    for expected in {"north_drawer", "south_drawer", "atrium_entry",
                     "atrium_changelog", "drawer_merge_log", "day_room_map",
                     "atrium_blacklist_guard"}:
        assert expected in tables, f"missing {expected}"
```

- [ ] **Step 3: 跑测，应先 fail（文件不存在或 SQL 错）**

```bash
python -m pytest tests/palace/test_migration.py -v
```
Expected: FAIL

- [ ] **Step 4: 修 SQL 直到过**

```bash
python -m pytest tests/palace/test_migration.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mybot/palace/__init__.py mybot/palace/migrations/001_init.sql tests/palace/test_migration.py
git commit -m "palace: add schema migration 001_init.sql"
```

---

### Task 0.3: Config 与 ids 模块

**Files:**
- Create: `mybot/palace/config.py`
- Create: `mybot/palace/ids.py`
- Modify: `config.yaml` (追加 palace 段，完整抄 spec §3.3)

- [ ] **Step 1: 测 ids 双向转换**

Create `tests/palace/test_ids.py`:
```python
from mybot.palace.ids import make_id, parse_id, Tower

def test_north_id_roundtrip():
    cid = make_id(Tower.NORTH, 2026, 107, 5, 7)
    assert cid == "N-2026-107-05-07"
    t, y, f, r, d = parse_id(cid)
    assert (t, y, f, r, d) == (Tower.NORTH, 2026, 107, 5, 7)

def test_south_id_padding():
    assert make_id(Tower.SOUTH, 2026, 1, 1, 1) == "S-2026-001-01-01"

def test_parse_invalid():
    import pytest
    with pytest.raises(ValueError):
        parse_id("garbage")
```

- [ ] **Step 2: 实现 ids.py**

```python
"""Coordinate ID helpers: N-YYYY-FFF-RR-DD / S-YYYY-FFF-RR-DD."""
from __future__ import annotations
from enum import Enum
from typing import Tuple
import re


class Tower(str, Enum):
    NORTH = "N"
    SOUTH = "S"


_ID_RE = re.compile(r"^([NS])-(\d{4})-(\d{3})-(\d{2})-(\d{2})$")


def make_id(tower: Tower, year: int, floor: int, room: int, drawer: int) -> str:
    if not (1 <= floor <= 365):
        raise ValueError(f"floor out of range: {floor}")
    if not (1 <= room <= 20):
        raise ValueError(f"room out of range: {room}")
    if not (1 <= drawer <= 20):
        raise ValueError(f"drawer out of range: {drawer}")
    return f"{tower.value}-{year:04d}-{floor:03d}-{room:02d}-{drawer:02d}"


def parse_id(cid: str) -> Tuple[Tower, int, int, int, int]:
    m = _ID_RE.match(cid)
    if not m:
        raise ValueError(f"invalid coord id: {cid!r}")
    t, y, f, r, d = m.groups()
    return Tower(t), int(y), int(f), int(r), int(d)
```

- [ ] **Step 3: 跑测**

```bash
python -m pytest tests/palace/test_ids.py -v
```
Expected: 3 passed

- [ ] **Step 4: 写 PalaceConfig dataclass**

Create `mybot/palace/config.py`：
```python
from __future__ import annotations
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List


@dataclass
class AtriumGuards:
    blacklist_patterns: List[str] = field(default_factory=lambda: [
        "不可用", "未能找到", "服务中断", "超时",
        "工具报错", "无法访问", "操作失败", "连接失败",
    ])
    evidence_threshold: int = 3
    evidence_days_span: int = 2
    require_manual_approve: bool = True
    review_cycle_days: int = 30
    stale_archive_days: int = 90


@dataclass
class PalaceConfig:
    db_path: Path = Path("data/palace.db")
    current_year_scope: int = 3
    embedder: str = "BAAI/bge-m3"
    embedder_dim: int = 1024
    reranker: str = "BAAI/bge-reranker-v2-m3"
    top_k_south: int = 5
    top_k_fact: int = 3
    fixed_rooms: Dict[int, str] = field(default_factory=lambda: {
        1: "消费", 2: "工作", 3: "人际", 4: "健康", 5: "学习",
        6: "技术", 7: "项目", 8: "家庭", 9: "出行", 10: "情绪",
    })
    misc_room: int = 20
    guards: AtriumGuards = field(default_factory=AtriumGuards)

    @classmethod
    def from_dict(cls, d: dict) -> "PalaceConfig":
        cfg = cls()
        p = d.get("palace", {})
        if "db_path" in p: cfg.db_path = Path(p["db_path"])
        if "current_year_scope" in p: cfg.current_year_scope = int(p["current_year_scope"])
        if "embedder" in p: cfg.embedder = p["embedder"]
        if "embedder_dim" in p: cfg.embedder_dim = int(p["embedder_dim"])
        if "reranker" in p: cfg.reranker = p["reranker"]
        if "top_k_south" in p: cfg.top_k_south = int(p["top_k_south"])
        if "top_k_fact" in p: cfg.top_k_fact = int(p["top_k_fact"])
        rooms = d.get("rooms", {})
        if "fixed" in rooms:
            cfg.fixed_rooms = {int(k): v for k, v in rooms["fixed"].items()}
        if "misc_room" in rooms: cfg.misc_room = int(rooms["misc_room"])
        g = d.get("atrium_guards", {})
        if g:
            cfg.guards = AtriumGuards(
                blacklist_patterns=g.get("blacklist_patterns", cfg.guards.blacklist_patterns),
                evidence_threshold=int(g.get("evidence_threshold", cfg.guards.evidence_threshold)),
                evidence_days_span=int(g.get("evidence_days_span", cfg.guards.evidence_days_span)),
                require_manual_approve=bool(g.get("require_manual_approve", cfg.guards.require_manual_approve)),
                review_cycle_days=int(g.get("review_cycle_days", cfg.guards.review_cycle_days)),
                stale_archive_days=int(g.get("stale_archive_days", cfg.guards.stale_archive_days)),
            )
        return cfg
```

- [ ] **Step 5: 追加 palace 段到 config.yaml**

追加 spec §3.3 的 YAML 块（palace / rooms / atrium_guards / telegram_notify），注意键名跟 PalaceConfig.from_dict 对齐。

- [ ] **Step 6: 测 config 加载**

追加到 `tests/palace/test_migration.py` 末尾：
```python
def test_config_from_yaml():
    import yaml
    from mybot.palace.config import PalaceConfig
    cfg_dict = yaml.safe_load(open("config.yaml"))
    cfg = PalaceConfig.from_dict(cfg_dict)
    assert cfg.db_path.name == "palace.db"
    assert cfg.fixed_rooms[1] == "消费"
    assert "不可用" in cfg.guards.blacklist_patterns
```

```bash
python -m pytest tests/palace/test_migration.py -v
```
Expected: 全 passed

- [ ] **Step 7: Commit**

```bash
git add mybot/palace/config.py mybot/palace/ids.py tests/palace/test_ids.py tests/palace/test_migration.py config.yaml
git commit -m "palace: add PalaceConfig + coord ID helpers"
```

---

## Phase 1 — Store（持久层）

### Task 1.1: PalaceStore 初始化 + 连接管理

**Files:**
- Create: `mybot/palace/store.py`
- Create: `tests/palace/conftest.py`

- [ ] **Step 1: 写 conftest fixtures**

```python
# tests/palace/conftest.py
import pytest
import pytest_asyncio
from pathlib import Path
from mybot.palace.config import PalaceConfig
from mybot.palace.store import PalaceStore


@pytest_asyncio.fixture
async def store(tmp_path):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    s = PalaceStore(cfg)
    await s.initialize()
    yield s
    await s.close()


class FakeLLM:
    """Scripted LLM: pop responses in order."""
    def __init__(self, responses: list[str]):
        self.responses = list(responses)
        self.calls = []

    async def __call__(self, messages):
        self.calls.append(messages)
        if not self.responses:
            raise RuntimeError("FakeLLM out of responses")
        return self.responses.pop(0)


@pytest.fixture
def fake_llm():
    return FakeLLM([])


class FakeEmbedder:
    """Deterministic embedding: hash(text) → 1024-dim."""
    def __init__(self, dim: int = 1024):
        self.dim = dim

    def encode(self, texts):
        import numpy as np
        if isinstance(texts, str):
            texts = [texts]
        out = []
        for t in texts:
            rng = np.random.default_rng(abs(hash(t)) % (2**32))
            v = rng.standard_normal(self.dim).astype("float32")
            v /= (np.linalg.norm(v) + 1e-8)
            out.append(v)
        return np.stack(out)


@pytest.fixture
def fake_embedder():
    return FakeEmbedder()
```

- [ ] **Step 2: 测 initialize 能跑 migration、二次 initialize 不报错**

```python
# tests/palace/test_store.py
import pytest

@pytest.mark.asyncio
async def test_store_initialize_idempotent(store):
    # already initialized in fixture
    await store.initialize()  # second call must not fail
    # tables present
    async with store.acquire() as conn:
        rows = await (await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )).fetchall()
    names = {r[0] for r in rows}
    assert "north_drawer" in names
    assert "south_drawer" in names
    assert "atrium_entry" in names
```

- [ ] **Step 3: 实现 PalaceStore 初版（只含 initialize / acquire / close）**

```python
# mybot/palace/store.py
from __future__ import annotations

import aiosqlite
import sqlite_vec
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

from .config import PalaceConfig


MIGRATION_PATH = Path(__file__).parent / "migrations" / "001_init.sql"


class PalaceStore:
    def __init__(self, cfg: PalaceConfig):
        self.cfg = cfg
        self.db_path: Path = cfg.db_path
        self._initialized = False

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with self._raw_connect() as conn:
            row = await (await conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='north_drawer'"
            )).fetchone()
            if row is None:
                sql = MIGRATION_PATH.read_text()
                await conn.executescript(sql)
                await conn.commit()
        self._initialized = True

    @asynccontextmanager
    async def _raw_connect(self) -> AsyncIterator[aiosqlite.Connection]:
        conn = await aiosqlite.connect(self.db_path)
        await conn.enable_load_extension(True)
        # sqlite-vec ships a Python loader that points at the .dylib path
        await conn.load_extension(sqlite_vec.loadable_path())
        await conn.enable_load_extension(False)
        try:
            yield conn
        finally:
            await conn.close()

    @asynccontextmanager
    async def acquire(self) -> AsyncIterator[aiosqlite.Connection]:
        if not self._initialized:
            await self.initialize()
        async with self._raw_connect() as conn:
            yield conn

    async def close(self) -> None:
        # no pool yet; placeholder for future
        pass
```

- [ ] **Step 4: 跑测**

```bash
python -m pytest tests/palace/test_store.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mybot/palace/store.py tests/palace/conftest.py tests/palace/test_store.py
git commit -m "palace: add PalaceStore with migration-on-init"
```

---

### Task 1.2: 北塔 CRUD

**Files:**
- Modify: `mybot/palace/store.py` (append methods)
- Modify: `tests/palace/test_store.py`

- [ ] **Step 1: 测 `insert_north_drawer` + `get_north_drawer`**

```python
@pytest.mark.asyncio
async def test_north_insert_and_get(store):
    drawer_id = await store.insert_north_drawer(
        year=2026, floor=107, room=5, drawer=7,
        date="2026-04-16",
        raw_messages=[{"role": "user", "content": "hi"}],
    )
    assert drawer_id == "N-2026-107-05-07"
    row = await store.get_north_drawer(drawer_id)
    assert row["date"] == "2026-04-16"
    assert row["raw_messages"][0]["content"] == "hi"
    assert row["message_count"] == 1

@pytest.mark.asyncio
async def test_north_unique_coord(store):
    await store.insert_north_drawer(year=2026, floor=1, room=1, drawer=1,
                                    date="2026-01-01", raw_messages=[])
    import aiosqlite
    with pytest.raises(aiosqlite.IntegrityError):
        await store.insert_north_drawer(year=2026, floor=1, room=1, drawer=1,
                                        date="2026-01-01", raw_messages=[])
```

- [ ] **Step 2: 实现**

追加到 `store.py`:
```python
    async def insert_north_drawer(
        self, *, year: int, floor: int, room: int, drawer: int,
        date: str, raw_messages: list[dict]
    ) -> str:
        import json
        from .ids import make_id, Tower
        drawer_id = make_id(Tower.NORTH, year, floor, room, drawer)
        async with self.acquire() as conn:
            await conn.execute(
                """INSERT INTO north_drawer
                   (id, year, floor, room, drawer, date, raw_messages, message_count)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (drawer_id, year, floor, room, drawer, date,
                 json.dumps(raw_messages, ensure_ascii=False),
                 len(raw_messages)),
            )
            await conn.commit()
        return drawer_id

    async def get_north_drawer(self, drawer_id: str) -> dict | None:
        import json
        async with self.acquire() as conn:
            row = await (await conn.execute(
                "SELECT * FROM north_drawer WHERE id=?", (drawer_id,)
            )).fetchone()
            if row is None:
                return None
            cols = [c[0] for c in (await conn.execute(
                "PRAGMA table_info(north_drawer)"
            )).description]
        rec = dict(zip(cols, row))
        rec["raw_messages"] = json.loads(rec["raw_messages"])
        return rec
```

- [ ] **Step 3: 跑测**

```bash
python -m pytest tests/palace/test_store.py -v
```
Expected: 新测 2 passed

- [ ] **Step 4: Commit**

```bash
git add mybot/palace/store.py tests/palace/test_store.py
git commit -m "palace: insert/get north drawer"
```

---

### Task 1.3: 南塔 CRUD + FTS 自动同步

**Files:**
- Modify: `mybot/palace/store.py`
- Modify: `mybot/palace/migrations/001_init.sql`（追加 FTS 同步触发器）
- Modify: `tests/palace/test_store.py`

- [ ] **Step 1: 给 migration 加 FTS 同步触发器**

追加到 001_init.sql 末尾：
```sql
-- Keep south_fts in sync with south_drawer
CREATE TRIGGER south_fts_ai AFTER INSERT ON south_drawer BEGIN
    INSERT INTO south_fts(drawer_id, summary, keywords)
    VALUES (NEW.id, NEW.summary, COALESCE(NEW.keywords, ''));
END;
CREATE TRIGGER south_fts_ad AFTER DELETE ON south_drawer BEGIN
    DELETE FROM south_fts WHERE drawer_id = OLD.id;
END;
CREATE TRIGGER south_fts_au AFTER UPDATE ON south_drawer BEGIN
    DELETE FROM south_fts WHERE drawer_id = OLD.id;
    INSERT INTO south_fts(drawer_id, summary, keywords)
    VALUES (NEW.id, NEW.summary, COALESCE(NEW.keywords, ''));
END;
```

- [ ] **Step 2: 测 `insert_south_drawer`（含向量）+ `get_south_drawer` + FTS 能命中**

```python
@pytest.mark.asyncio
async def test_south_insert_and_fts(store, fake_embedder):
    import numpy as np
    emb = fake_embedder.encode("北京消费讨论")[0]
    drawer_id = await store.insert_south_drawer(
        year=2026, floor=107, room=1, drawer=1,
        date="2026-04-16",
        north_ref_ids=["N-2026-107-01-01"],
        room_type="fixed", room_label="消费",
        drawer_topic="北京消费问答",
        summary="用户问在北京花了多少钱，核算出 69 万元",
        keywords=["北京", "消费", "69 万"],
        embedding=emb,
    )
    assert drawer_id == "S-2026-107-01-01"
    hits = await store.fts_search("北京", limit=5)
    assert any(h["drawer_id"] == drawer_id for h in hits)
```

- [ ] **Step 3: 实现**

```python
    async def insert_south_drawer(
        self, *, year: int, floor: int, room: int, drawer: int,
        date: str, north_ref_ids: list[str],
        room_type: str, room_label: str, drawer_topic: str,
        summary: str, keywords: list[str],
        embedding,  # np.ndarray shape (dim,)
    ) -> str:
        import json
        from .ids import make_id, Tower
        drawer_id = make_id(Tower.SOUTH, year, floor, room, drawer)
        kw_json = json.dumps(keywords, ensure_ascii=False)
        async with self.acquire() as conn:
            await conn.execute(
                """INSERT INTO south_drawer
                   (id, north_ref_ids, year, floor, room, drawer, date,
                    room_type, room_label, drawer_topic, summary, keywords)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (drawer_id, json.dumps(north_ref_ids), year, floor, room, drawer,
                 date, room_type, room_label, drawer_topic, summary, kw_json),
            )
            await conn.execute(
                "INSERT INTO south_vec(drawer_id, embedding) VALUES (?, ?)",
                (drawer_id, _pack_float32(embedding)),
            )
            await conn.commit()
        return drawer_id

    async def fts_search(self, query: str, limit: int = 30) -> list[dict]:
        async with self.acquire() as conn:
            rows = await (await conn.execute(
                """SELECT drawer_id, bm25(south_fts) AS score
                   FROM south_fts
                   WHERE south_fts MATCH ?
                   ORDER BY score
                   LIMIT ?""",
                (query, limit),
            )).fetchall()
        return [{"drawer_id": r[0], "score": r[1]} for r in rows]
```

在模块顶部加：
```python
import struct
def _pack_float32(arr) -> bytes:
    """Pack a 1-D float32 array as bytes for sqlite-vec."""
    import numpy as np
    a = np.asarray(arr, dtype="float32")
    return a.tobytes()
```

- [ ] **Step 4: 重建 fixture 的 palace.db（因为改了 migration）**

不需要 —— fixture 每次跑都是 `tmp_path`。直接跑：
```bash
python -m pytest tests/palace/test_store.py -v
```
Expected: 新测 PASS

- [ ] **Step 5: Commit**

```bash
git add mybot/palace/store.py mybot/palace/migrations/001_init.sql tests/palace/test_store.py
git commit -m "palace: insert south drawer with vec+fts sync"
```

---

### Task 1.4: 向量 KNN 查询

**Files:**
- Modify: `mybot/palace/store.py`
- Modify: `tests/palace/test_store.py`

- [ ] **Step 1: 测 `vec_knn`**

```python
@pytest.mark.asyncio
async def test_vec_knn(store, fake_embedder):
    texts = ["北京消费讨论", "上海工作安排", "深圳旅游计划"]
    for i, t in enumerate(texts):
        emb = fake_embedder.encode(t)[0]
        await store.insert_south_drawer(
            year=2026, floor=100+i, room=1, drawer=1,
            date=f"2026-04-{10+i:02d}",
            north_ref_ids=[f"N-2026-{100+i:03d}-01-01"],
            room_type="fixed", room_label="消费",
            drawer_topic=t, summary=t, keywords=[],
            embedding=emb,
        )
    q = fake_embedder.encode("北京消费讨论")[0]
    hits = await store.vec_knn(q, limit=3)
    # Top-1 必须是"北京消费讨论"本身
    assert hits[0]["drawer_id"] == "S-2026-100-01-01"
```

- [ ] **Step 2: 实现**

```python
    async def vec_knn(self, query_emb, limit: int = 30,
                      year_min: int | None = None) -> list[dict]:
        sql = ("SELECT drawer_id, distance FROM south_vec "
               "WHERE embedding MATCH ? AND k = ? ")
        params = [_pack_float32(query_emb), limit]
        async with self.acquire() as conn:
            if year_min is not None:
                # sqlite-vec: combine KNN with metadata filter via JOIN
                sql = """SELECT sv.drawer_id, sv.distance
                         FROM south_vec sv
                         JOIN south_drawer sd ON sv.drawer_id = sd.id
                         WHERE sv.embedding MATCH ? AND sv.k = ?
                           AND sd.year >= ?
                         ORDER BY sv.distance"""
                params = [_pack_float32(query_emb), limit, year_min]
            rows = await (await conn.execute(sql, params)).fetchall()
        return [{"drawer_id": r[0], "distance": r[1]} for r in rows]
```

- [ ] **Step 3: 跑测**

```bash
python -m pytest tests/palace/test_store.py::test_vec_knn -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add mybot/palace/store.py tests/palace/test_store.py
git commit -m "palace: add vec KNN query"
```

---

### Task 1.5: 中庭 CRUD + 黑名单触发器验证

**Files:**
- Modify: `mybot/palace/store.py`
- Create: `tests/palace/test_atrium_guards.py`

- [ ] **Step 1: 测黑名单触发器必须阻断 8 个 pattern**

```python
# tests/palace/test_atrium_guards.py
import pytest
import aiosqlite


PATTERNS = ["不可用", "未能找到", "服务中断", "超时",
            "工具报错", "无法访问", "操作失败", "连接失败"]


@pytest.mark.asyncio
@pytest.mark.parametrize("pat", PATTERNS)
async def test_blacklist_trigger_rejects(store, pat):
    import uuid
    with pytest.raises(aiosqlite.IntegrityError) as ei:
        await store.insert_atrium_entry(
            id=str(uuid.uuid4()),
            entry_type="rule",
            content=f"前缀{pat}后缀",
            source_type="explicit",
            status="active",
        )
    assert "blacklist" in str(ei.value).lower()


@pytest.mark.asyncio
async def test_blacklist_allows_clean(store):
    import uuid
    aid = await store.insert_atrium_entry(
        id=str(uuid.uuid4()),
        entry_type="preference",
        content="用户偏好简洁直接回答",
        source_type="explicit", status="active",
    )
    entry = await store.get_atrium_entry(aid)
    assert entry["status"] == "active"
```

- [ ] **Step 2: 实现 atrium CRUD**

```python
    async def insert_atrium_entry(
        self, *, id: str, entry_type: str, content: str,
        source_type: str, status: str,
        evidence_drawer_ids: list[str] | None = None,
        confidence: float = 1.0,
        embedding=None,
    ) -> str:
        import json
        from datetime import datetime
        now = datetime.utcnow().isoformat()
        async with self.acquire() as conn:
            await conn.execute(
                """INSERT INTO atrium_entry
                   (id, entry_type, content, source_type, status,
                    evidence_drawer_ids, evidence_count, confidence,
                    proposed_at, approved_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (id, entry_type, content, source_type, status,
                 json.dumps(evidence_drawer_ids or []),
                 len(evidence_drawer_ids or []), confidence,
                 now, now if status == "active" else None),
            )
            if embedding is not None:
                await conn.execute(
                    "INSERT INTO atrium_vec(entry_id, embedding) VALUES (?,?)",
                    (id, _pack_float32(embedding)),
                )
            # changelog
            await conn.execute(
                """INSERT INTO atrium_changelog(entry_id, old_value, new_value, action, actor)
                   VALUES (?, NULL, ?, 'create', ?)""",
                (id, json.dumps({"content": content, "status": status}),
                 "user_cli" if source_type == "explicit" else "auto_proposer"),
            )
            await conn.commit()
        return id

    async def get_atrium_entry(self, entry_id: str) -> dict | None:
        import json
        async with self.acquire() as conn:
            cursor = await conn.execute(
                "SELECT * FROM atrium_entry WHERE id=?", (entry_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            cols = [c[0] for c in cursor.description]
        rec = dict(zip(cols, row))
        rec["evidence_drawer_ids"] = json.loads(rec.get("evidence_drawer_ids") or "[]")
        return rec

    async def list_atrium_entries(
        self, *, status: str | None = None, entry_type: str | None = None,
    ) -> list[dict]:
        import json
        sql = "SELECT * FROM atrium_entry WHERE 1=1"
        params = []
        if status:
            sql += " AND status=?"; params.append(status)
        if entry_type:
            sql += " AND entry_type=?"; params.append(entry_type)
        sql += " ORDER BY proposed_at DESC"
        async with self.acquire() as conn:
            cursor = await conn.execute(sql, params)
            rows = await cursor.fetchall()
            cols = [c[0] for c in cursor.description]
        out = []
        for r in rows:
            rec = dict(zip(cols, r))
            rec["evidence_drawer_ids"] = json.loads(rec.get("evidence_drawer_ids") or "[]")
            out.append(rec)
        return out

    async def update_atrium_status(
        self, entry_id: str, new_status: str, *, actor: str = "user_cli",
    ) -> None:
        import json
        from datetime import datetime
        now = datetime.utcnow().isoformat()
        async with self.acquire() as conn:
            old = await self.get_atrium_entry(entry_id)
            if old is None:
                raise ValueError(f"no atrium entry {entry_id}")
            col = {"active": "approved_at", "rejected": "rejected_at",
                   "archived": "last_reviewed_at"}.get(new_status, "updated_at")
            await conn.execute(
                f"UPDATE atrium_entry SET status=?, {col}=?, updated_at=? WHERE id=?",
                (new_status, now, now, entry_id),
            )
            await conn.execute(
                """INSERT INTO atrium_changelog(entry_id, old_value, new_value, action, actor)
                   VALUES (?, ?, ?, ?, ?)""",
                (entry_id, json.dumps({"status": old["status"]}),
                 json.dumps({"status": new_status}),
                 {"active": "approve", "rejected": "reject",
                  "archived": "archive"}.get(new_status, "update"),
                 actor),
            )
            await conn.commit()
```

- [ ] **Step 3: 跑测**

```bash
python -m pytest tests/palace/test_atrium_guards.py -v
```
Expected: 9 passed (8 params + 1 clean)

- [ ] **Step 4: Commit**

```bash
git add mybot/palace/store.py tests/palace/test_atrium_guards.py
git commit -m "palace: atrium CRUD + blacklist trigger tests"
```

---

### Task 1.6: day_room_map 读写 + merge_log

**Files:**
- Modify: `mybot/palace/store.py`
- Modify: `tests/palace/test_store.py`

- [ ] **Step 1: 测 `get_day_room_map` / `upsert_day_room`**

```python
@pytest.mark.asyncio
async def test_day_room_map(store):
    await store.upsert_day_room(
        date="2026-04-16", room=1, room_type="fixed",
        room_label="消费", drawer_count=3,
    )
    rooms = await store.get_day_room_map("2026-04-16")
    assert rooms[1]["room_label"] == "消费"
    assert rooms[1]["drawer_count"] == 3
    # 增量更新
    await store.upsert_day_room(
        date="2026-04-16", room=1, room_type="fixed",
        room_label="消费", drawer_count=4,
    )
    rooms = await store.get_day_room_map("2026-04-16")
    assert rooms[1]["drawer_count"] == 4
```

- [ ] **Step 2: 实现**

```python
    async def upsert_day_room(
        self, *, date: str, room: int, room_type: str,
        room_label: str, drawer_count: int,
    ) -> None:
        async with self.acquire() as conn:
            await conn.execute(
                """INSERT INTO day_room_map(date, room, room_type, room_label, drawer_count)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(date, room) DO UPDATE SET
                     room_type=excluded.room_type,
                     room_label=excluded.room_label,
                     drawer_count=excluded.drawer_count""",
                (date, room, room_type, room_label, drawer_count),
            )
            await conn.commit()

    async def get_day_room_map(self, date: str) -> dict[int, dict]:
        async with self.acquire() as conn:
            cursor = await conn.execute(
                "SELECT room, room_type, room_label, drawer_count "
                "FROM day_room_map WHERE date=?", (date,),
            )
            rows = await cursor.fetchall()
        return {r[0]: {"room_type": r[1], "room_label": r[2], "drawer_count": r[3]}
                for r in rows}

    async def log_drawer_merge(
        self, *, target_id: str, merged_from: list[dict], reason: str,
    ) -> None:
        import json
        async with self.acquire() as conn:
            await conn.execute(
                "INSERT INTO drawer_merge_log(target_id, merged_from, reason) VALUES (?,?,?)",
                (target_id, json.dumps(merged_from, ensure_ascii=False), reason),
            )
            await conn.commit()
```

- [ ] **Step 3: 跑测**

```bash
python -m pytest tests/palace/test_store.py -v
```
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add mybot/palace/store.py tests/palace/test_store.py
git commit -m "palace: day_room_map + merge_log helpers"
```

---

## Phase 2 — Embedder & Reranker

### Task 2.1: Embedder（bge-m3）

**Files:**
- Create: `mybot/palace/embedder.py`
- Create: `tests/palace/test_embedder.py`

- [ ] **Step 1: 测接口契约（单测用 FakeEmbedder 已够；这里测真实加载 smoke，慢不跑默认）**

```python
# tests/palace/test_embedder.py
import pytest

def test_embedder_interface_contract():
    """纯接口契约测试，不触发模型下载。"""
    from mybot.palace.embedder import Embedder
    assert hasattr(Embedder, "encode")
    assert hasattr(Embedder, "dim")


@pytest.mark.slow
def test_embedder_bge_m3_smoke():
    """真加载 bge-m3，耗时 + 占空间。"""
    from mybot.palace.embedder import Embedder
    e = Embedder(model_name="BAAI/bge-m3", dim=1024)
    v = e.encode("北京消费")
    assert v.shape == (1, 1024)
    v2 = e.encode(["北京消费", "上海工作"])
    assert v2.shape == (2, 1024)
```

追加到 `pyproject.toml`（如果还没加 pytest markers）：
```toml
[tool.pytest.ini_options]
markers = [
    "slow: marks tests as slow (deselect with '-m \"not slow\"')",
]
asyncio_mode = "auto"
```

- [ ] **Step 2: 实现 embedder.py**

```python
"""Local embedder wrapper around FlagEmbedding.BGEM3FlagModel."""
from __future__ import annotations
import logging
from typing import Sequence
import numpy as np


logger = logging.getLogger(__name__)


class Embedder:
    """Wraps BAAI/bge-m3. Lazy-loads on first encode()."""

    def __init__(self, model_name: str = "BAAI/bge-m3", dim: int = 1024,
                 use_fp16: bool = True):
        self.model_name = model_name
        self.dim = dim
        self.use_fp16 = use_fp16
        self._model = None

    def _lazy_load(self):
        if self._model is not None:
            return
        from FlagEmbedding import BGEM3FlagModel
        logger.info("loading embedder %s (may download ~2GB first time)", self.model_name)
        self._model = BGEM3FlagModel(self.model_name, use_fp16=self.use_fp16)

    def encode(self, texts) -> np.ndarray:
        """Returns shape (N, dim), L2-normalized float32."""
        self._lazy_load()
        if isinstance(texts, str):
            texts = [texts]
        out = self._model.encode(
            list(texts), max_length=512, return_dense=True,
        )["dense_vecs"]
        arr = np.asarray(out, dtype="float32")
        # bge-m3 already returns normalized, but be safe:
        norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8
        return arr / norms
```

- [ ] **Step 3: 跑快测**

```bash
python -m pytest tests/palace/test_embedder.py -v -m 'not slow'
```
Expected: 1 passed (contract), 1 deselected

- [ ] **Step 4: 在后台下载 bge-m3 模型（可与后续 Task 并行）**

```bash
python -c "from FlagEmbedding import BGEM3FlagModel; BGEM3FlagModel('BAAI/bge-m3', use_fp16=True)" &
```
Expected: 下载到 `~/.cache/huggingface/hub/`（~2GB）。后续 Task 无需等完成。

- [ ] **Step 5: Commit**

```bash
git add mybot/palace/embedder.py tests/palace/test_embedder.py pyproject.toml
git commit -m "palace: add bge-m3 Embedder wrapper (lazy load)"
```

---

### Task 2.2: Reranker

**Files:**
- Create: `mybot/palace/reranker.py`
- Create: `tests/palace/test_reranker.py`

- [ ] **Step 1: 测接口契约**

```python
# tests/palace/test_reranker.py
def test_reranker_interface():
    from mybot.palace.reranker import Reranker
    assert hasattr(Reranker, "rerank")
```

- [ ] **Step 2: 实现**

```python
# mybot/palace/reranker.py
from __future__ import annotations
import logging
from typing import List, Tuple
import numpy as np


logger = logging.getLogger(__name__)


class Reranker:
    def __init__(self, model_name: str = "BAAI/bge-reranker-v2-m3",
                 use_fp16: bool = True):
        self.model_name = model_name
        self.use_fp16 = use_fp16
        self._model = None

    def _lazy_load(self):
        if self._model is not None:
            return
        from FlagEmbedding import FlagReranker
        logger.info("loading reranker %s (may download ~1GB first time)", self.model_name)
        self._model = FlagReranker(self.model_name, use_fp16=self.use_fp16)

    def rerank(self, query: str, docs: List[str]) -> List[float]:
        if not docs:
            return []
        self._lazy_load()
        pairs = [[query, d] for d in docs]
        scores = self._model.compute_score(pairs, normalize=True)
        if isinstance(scores, float):
            return [float(scores)]
        return [float(s) for s in scores]
```

- [ ] **Step 3: 跑测 + 后台下载 reranker**

```bash
python -m pytest tests/palace/test_reranker.py -v
python -c "from FlagEmbedding import FlagReranker; FlagReranker('BAAI/bge-reranker-v2-m3', use_fp16=True)" &
```

- [ ] **Step 4: Commit**

```bash
git add mybot/palace/reranker.py tests/palace/test_reranker.py
git commit -m "palace: add bge-reranker-v2-m3 Reranker wrapper"
```

---

## Phase 3 — Write Path

### Task 3.1: Chunker（切子话题 + 摘要）

**Files:**
- Create: `mybot/palace/chunker.py`
- Create: `tests/palace/test_chunker.py`
- Create: `tests/palace/fixtures/multi_topic_session.json`

- [ ] **Step 1: 建 fixture**

`tests/palace/fixtures/multi_topic_session.json`:
```json
[
  {"role": "user", "content": "我在北京花了多少钱？"},
  {"role": "assistant", "content": "根据账单，共 69 万元。"},
  {"role": "user", "content": "下周帮我安排三个会议"},
  {"role": "assistant", "content": "好，周二/周四/周五各一个？"}
]
```

- [ ] **Step 2: 测 chunker 返回结构**

```python
# tests/palace/test_chunker.py
import json
import pytest
from pathlib import Path
from mybot.palace.chunker import Chunker, Chunk


@pytest.mark.asyncio
async def test_chunker_basic(fake_llm):
    session = json.loads(Path("tests/palace/fixtures/multi_topic_session.json").read_text())
    fake_llm.responses = [json.dumps([
        {"msg_indices": [0, 1], "drawer_topic": "北京消费问答",
         "summary": "用户问在北京花了多少，算出 69 万元",
         "keywords": ["北京", "消费"], "proposed_room_label": "消费"},
        {"msg_indices": [2, 3], "drawer_topic": "下周会议安排",
         "summary": "安排三个会议：周二、四、五",
         "keywords": ["会议", "下周"], "proposed_room_label": "工作"},
    ])]
    chunker = Chunker(llm=fake_llm)
    chunks = await chunker.chunk_and_summarise(session)
    assert len(chunks) == 2
    assert chunks[0].proposed_room_label == "消费"
    assert chunks[0].summary.startswith("用户问")
```

- [ ] **Step 3: 实现 Chunker**

```python
# mybot/palace/chunker.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, List
import json
import logging
import re

logger = logging.getLogger(__name__)
LLMCallable = Callable[[list[dict[str, Any]]], Awaitable[str]]


@dataclass
class Chunk:
    msg_indices: list[int]
    drawer_topic: str
    summary: str
    keywords: list[str]
    proposed_room_label: str


CHUNK_PROMPT = """你是一个对话切分与摘要代理。任务：
1. 把下面这一段对话切成若干"子话题 chunk"，每个 chunk 是连续的消息范围
2. 每个 chunk 给一个简短的 drawer_topic（≤15 字）
3. 每个 chunk 输出 summary（≤200 字，客观第三人称陈述）
4. 抽取 3-8 个 keywords
5. 给出 proposed_room_label，从以下 10 个固定类别里选最接近的；都不合适就自拟一个简短中文标签（≤4 字）：
   消费 / 工作 / 人际 / 健康 / 学习 / 技术 / 项目 / 家庭 / 出行 / 情绪

对话内容（每条消息前面是它的全局索引）：
---
{convo}
---

严格输出一个 JSON 数组，每个元素：
{{
  "msg_indices": [0, 1],
  "drawer_topic": "...",
  "summary": "...",
  "keywords": ["..."],
  "proposed_room_label": "..."
}}
不要 markdown 代码块。没有可归档内容则输出 []。
"""


class Chunker:
    def __init__(self, llm: LLMCallable):
        self.llm = llm

    async def chunk_and_summarise(self, messages: list[dict]) -> List[Chunk]:
        if not messages:
            return []
        rendered = "\n".join(
            f"[{i}] {m.get('role','user')}: {str(m.get('content',''))[:500]}"
            for i, m in enumerate(messages)
        )
        prompt = CHUNK_PROMPT.format(convo=rendered)
        try:
            raw = await self.llm([{"role": "user", "content": prompt}])
        except Exception as exc:
            logger.warning("chunker LLM failed: %s", exc)
            return []
        items = _parse_json_array(raw)
        out: list[Chunk] = []
        for it in items:
            if not isinstance(it, dict):
                continue
            try:
                out.append(Chunk(
                    msg_indices=[int(i) for i in it.get("msg_indices", [])],
                    drawer_topic=str(it.get("drawer_topic", "未命名")).strip()[:40] or "未命名",
                    summary=str(it.get("summary", "")).strip()[:300],
                    keywords=[str(k).strip() for k in (it.get("keywords") or [])][:10],
                    proposed_room_label=str(it.get("proposed_room_label", "杂项")).strip()[:10] or "杂项",
                ))
            except Exception as exc:
                logger.warning("chunker item parse failed: %s", exc)
        return out


def _parse_json_array(raw: str) -> list[Any]:
    text = (raw or "").strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
    if fence:
        text = fence.group(1)
    try:
        obj = json.loads(text)
        return obj if isinstance(obj, list) else []
    except json.JSONDecodeError:
        start = text.find("[")
        if start < 0:
            return []
        depth = 0
        for i in range(start, len(text)):
            if text[i] == "[": depth += 1
            elif text[i] == "]":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(text[start:i+1])
                        return obj if isinstance(obj, list) else []
                    except json.JSONDecodeError:
                        return []
        return []
```

- [ ] **Step 4: 跑测**

```bash
python -m pytest tests/palace/test_chunker.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mybot/palace/chunker.py tests/palace/test_chunker.py tests/palace/fixtures/multi_topic_session.json
git commit -m "palace: add Chunker (LLM-backed split + summarise)"
```

---

### Task 3.2: Router（房间路由 + 溢出合并）

**Files:**
- Create: `mybot/palace/router.py`
- Create: `tests/palace/test_router.py`

- [ ] **Step 1: 测固定房映射 / 动态房复用 / 杂项兜底**

```python
# tests/palace/test_router.py
import pytest
from mybot.palace.router import Router
from mybot.palace.config import PalaceConfig


@pytest.mark.asyncio
async def test_fixed_room_mapping(store):
    cfg = PalaceConfig()
    router = Router(cfg, store)
    slot = await router.assign_room(date="2026-04-16", proposed_label="消费")
    assert slot.room == 1
    assert slot.room_type == "fixed"
    assert slot.room_label == "消费"


@pytest.mark.asyncio
async def test_dynamic_room_reuse(store):
    cfg = PalaceConfig()
    router = Router(cfg, store)
    s1 = await router.assign_room(date="2026-04-16", proposed_label="书法练习")
    assert s1.room_type == "dynamic"
    assert 11 <= s1.room <= 19
    s2 = await router.assign_room(date="2026-04-16", proposed_label="书法练习")
    assert s2.room == s1.room  # reused


@pytest.mark.asyncio
async def test_misc_overflow(store):
    cfg = PalaceConfig()
    router = Router(cfg, store)
    for i in range(9):
        await router.assign_room(
            date="2026-04-16", proposed_label=f"动态主题{i}"
        )
    slot = await router.assign_room(date="2026-04-16", proposed_label="再来一个新主题")
    assert slot.room == 20
    assert slot.room_type == "misc"
```

- [ ] **Step 2: 实现 Router（路由 + 抽屉分配，暂不实现合并）**

```python
# mybot/palace/router.py
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional
from .config import PalaceConfig
from .store import PalaceStore


@dataclass
class RoomSlot:
    room: int
    room_type: str    # 'fixed'|'dynamic'|'misc'
    room_label: str


@dataclass
class DrawerSlot:
    room: int
    drawer: int        # 1..20
    is_merge_target: bool = False  # 溢出时合并


class Router:
    def __init__(self, cfg: PalaceConfig, store: PalaceStore):
        self.cfg = cfg
        self.store = store
        self._fixed_label_to_room = {v: k for k, v in cfg.fixed_rooms.items()}

    async def assign_room(self, *, date: str, proposed_label: str) -> RoomSlot:
        rooms = await self.store.get_day_room_map(date)
        # 固定房命中
        if proposed_label in self._fixed_label_to_room:
            r = self._fixed_label_to_room[proposed_label]
            return RoomSlot(room=r, room_type="fixed", room_label=proposed_label)
        # 动态房：先看已有同 label
        for r, meta in rooms.items():
            if meta["room_type"] == "dynamic" and meta["room_label"] == proposed_label:
                return RoomSlot(room=r, room_type="dynamic", room_label=proposed_label)
        # 开新动态房
        used = {r for r, meta in rooms.items() if meta["room_type"] == "dynamic"}
        for r in range(11, 20):
            if r not in used:
                return RoomSlot(room=r, room_type="dynamic", room_label=proposed_label)
        # 兜底
        return RoomSlot(room=self.cfg.misc_room, room_type="misc",
                        room_label="杂项")

    async def assign_drawer(
        self, *, date: str, slot: RoomSlot,
    ) -> DrawerSlot:
        rooms = await self.store.get_day_room_map(date)
        count = rooms.get(slot.room, {}).get("drawer_count", 0)
        if count < 20:
            return DrawerSlot(room=slot.room, drawer=count + 1)
        return DrawerSlot(room=slot.room, drawer=-1, is_merge_target=True)
```

- [ ] **Step 3: 跑测**

```bash
python -m pytest tests/palace/test_router.py -v
```
Expected: 3 passed

- [ ] **Step 4: Commit**

```bash
git add mybot/palace/router.py tests/palace/test_router.py
git commit -m "palace: add Router (room + drawer assignment)"
```

---

### Task 3.3: Router 溢出合并（LLM merge）

**Files:**
- Modify: `mybot/palace/router.py`
- Modify: `tests/palace/test_router.py`
- Modify: `mybot/palace/store.py` (add `update_south_drawer_merge`)

- [ ] **Step 1: 测溢出时选 max-sim 抽屉 + 调 LLM 合并 + 追加 north_ref**

```python
@pytest.mark.asyncio
async def test_overflow_merge(store, fake_llm, fake_embedder):
    from mybot.palace.router import Router
    cfg = PalaceConfig()
    router = Router(cfg, store, embedder=fake_embedder, llm=fake_llm)

    # 先填满房间 1 的 20 抽屉
    for i in range(1, 21):
        emb = fake_embedder.encode(f"消费事件 {i}")[0]
        await store.insert_south_drawer(
            year=2026, floor=107, room=1, drawer=i,
            date="2026-04-16",
            north_ref_ids=[f"N-2026-107-01-{i:02d}"],
            room_type="fixed", room_label="消费",
            drawer_topic=f"事件{i}", summary=f"消费事件 {i}",
            keywords=["消费"], embedding=emb,
        )
    await store.upsert_day_room(date="2026-04-16", room=1,
                                 room_type="fixed", room_label="消费",
                                 drawer_count=20)

    # 21 号新 chunk；FakeLLM 返回合并摘要
    fake_llm.responses = ["合并后摘要：消费事件 1 与消费事件 21 合并陈述"]
    result = await router.merge_into_existing(
        date="2026-04-16",
        slot=RoomSlot(1, "fixed", "消费"),
        new_summary="消费事件 21",
        new_north_id="N-2026-107-01-21",
        new_embedding=fake_embedder.encode("消费事件 1")[0],  # 故意跟事件 1 一样
    )
    target = await store.get_south_drawer(result.target_south_id)
    assert "N-2026-107-01-21" in target["north_ref_ids"]
    assert target["merge_count"] == 2
```

- [ ] **Step 2: 实现合并接口 + store 合并方法**

在 `store.py` 加：
```python
    async def get_south_drawer(self, drawer_id: str) -> dict | None:
        import json
        async with self.acquire() as conn:
            cursor = await conn.execute(
                "SELECT * FROM south_drawer WHERE id=?", (drawer_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            cols = [c[0] for c in cursor.description]
        rec = dict(zip(cols, row))
        rec["north_ref_ids"] = json.loads(rec["north_ref_ids"])
        return rec

    async def list_room_south_drawers(
        self, *, date: str, room: int,
    ) -> list[dict]:
        import json
        async with self.acquire() as conn:
            cursor = await conn.execute(
                "SELECT * FROM south_drawer WHERE date=? AND room=?",
                (date, room),
            )
            rows = await cursor.fetchall()
            cols = [c[0] for c in cursor.description]
        out = []
        for r in rows:
            rec = dict(zip(cols, r))
            rec["north_ref_ids"] = json.loads(rec["north_ref_ids"])
            out.append(rec)
        return out

    async def merge_south_drawer(
        self, *, target_id: str, new_north_id: str,
        new_summary: str, new_embedding=None,
    ) -> None:
        import json
        async with self.acquire() as conn:
            cursor = await conn.execute(
                "SELECT north_ref_ids, merge_count FROM south_drawer WHERE id=?",
                (target_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                raise ValueError(f"no south drawer {target_id}")
            north_refs = json.loads(row[0])
            merge_count = row[1]
            north_refs.append(new_north_id)
            await conn.execute(
                "UPDATE south_drawer SET north_ref_ids=?, summary=?, merge_count=? "
                "WHERE id=?",
                (json.dumps(north_refs), new_summary,
                 merge_count + 1, target_id),
            )
            if new_embedding is not None:
                await conn.execute("DELETE FROM south_vec WHERE drawer_id=?", (target_id,))
                await conn.execute(
                    "INSERT INTO south_vec(drawer_id, embedding) VALUES (?,?)",
                    (target_id, _pack_float32(new_embedding)),
                )
            await conn.commit()
```

在 `router.py` 扩展 Router：
```python
@dataclass
class MergeResult:
    target_south_id: str
    merged_summary: str


MERGE_PROMPT = """请将两段关于同一主题的摘要合并成一段连贯的中文摘要，≤200 字，保留所有关键信息，不要遗漏：

【原摘要】
{old}

【新增摘要】
{new}

只输出合并后的摘要正文，不要加任何标签或 JSON。"""


class Router:
    def __init__(self, cfg, store, *, embedder=None, llm=None):
        self.cfg = cfg
        self.store = store
        self._fixed_label_to_room = {v: k for k, v in cfg.fixed_rooms.items()}
        self.embedder = embedder
        self.llm = llm

    # ... (assign_room / assign_drawer unchanged)

    async def merge_into_existing(
        self, *, date: str, slot: "RoomSlot",
        new_summary: str, new_north_id: str, new_embedding,
    ) -> "MergeResult":
        import numpy as np
        drawers = await self.store.list_room_south_drawers(date=date, room=slot.room)
        if not drawers:
            raise RuntimeError(f"room {slot.room} has no drawers to merge into")
        # 取 target 向量 → 跟 new_embedding 做 cosine（embedding 已归一化，用点积即可）
        sims = []
        for d in drawers:
            target_emb = await self.store.get_south_embedding(d["id"])
            sims.append((float(np.dot(target_emb, new_embedding)), d))
        sims.sort(key=lambda x: -x[0])
        target = sims[0][1]
        # LLM 合并
        prompt = MERGE_PROMPT.format(old=target["summary"], new=new_summary)
        merged = (await self.llm([{"role": "user", "content": prompt}])).strip()[:300] or target["summary"]
        await self.store.merge_south_drawer(
            target_id=target["id"],
            new_north_id=new_north_id,
            new_summary=merged,
            new_embedding=new_embedding,
        )
        await self.store.log_drawer_merge(
            target_id=target["id"],
            merged_from=[{"summary": target["summary"], "new_summary": new_summary,
                          "new_north_id": new_north_id}],
            reason="drawer_overflow",
        )
        return MergeResult(target_south_id=target["id"], merged_summary=merged)
```

在 `store.py` 加 `get_south_embedding`：
```python
    async def get_south_embedding(self, drawer_id: str):
        import numpy as np
        async with self.acquire() as conn:
            row = await (await conn.execute(
                "SELECT embedding FROM south_vec WHERE drawer_id=?", (drawer_id,)
            )).fetchone()
        if row is None:
            return None
        return np.frombuffer(row[0], dtype="float32")
```

- [ ] **Step 3: 跑测**

```bash
python -m pytest tests/palace/test_router.py -v
```
Expected: all pass

- [ ] **Step 4: Commit**

```bash
git add mybot/palace/router.py mybot/palace/store.py tests/palace/test_router.py
git commit -m "palace: Router overflow merge via LLM"
```

---

### Task 3.4: Writer（archive_session 编排）

**Files:**
- Create: `mybot/palace/writer.py`
- Create: `tests/palace/test_writer.py`
- Create: `tests/palace/fixtures/beijing_spending_session.json`

- [ ] **Step 1: 建 fixture**

```json
[
  {"role": "user", "content": "我在北京花了多少钱？"},
  {"role": "assistant", "content": "总计 69 万元。"},
  {"role": "user", "content": "记住：别启动 myontology/backend"}
]
```

- [ ] **Step 2: 测 archive_session 端到端 + 显式声明入中庭**

```python
# tests/palace/test_writer.py
import json, pytest
from pathlib import Path
from mybot.palace.writer import Writer
from mybot.palace.config import PalaceConfig


@pytest.mark.asyncio
async def test_archive_session_basic(store, fake_llm, fake_embedder):
    session = json.loads(Path("tests/palace/fixtures/beijing_spending_session.json").read_text())
    # LLM response: chunker 只切 1 段
    fake_llm.responses = [json.dumps([{
        "msg_indices": [0, 1, 2],
        "drawer_topic": "北京消费问答+规则",
        "summary": "用户问北京消费，算出 69 万元；并要求别启动 myontology/backend",
        "keywords": ["北京", "消费", "myontology"],
        "proposed_room_label": "消费",
    }])]
    cfg = PalaceConfig()
    w = Writer(cfg=cfg, store=store, llm=fake_llm, embedder=fake_embedder)
    result = await w.archive_session(session_id="test-1", messages=session,
                                      now_date="2026-04-16", now_year=2026)
    assert len(result.north_ids) == 1
    assert len(result.south_ids) == 1
    # 显式声明 "记住：别启动" 应触发 atrium 写入
    entries = await store.list_atrium_entries(status="active")
    assert any("myontology/backend" in e["content"] for e in entries)


@pytest.mark.asyncio
async def test_archive_session_blocks_blacklist(store, fake_llm, fake_embedder):
    """含有"不可用"叙述的 session，chunker 摘要里出现也只进南塔不进中庭。"""
    session = [
        {"role": "user", "content": "我在北京花了多少钱"},
        {"role": "assistant", "content": "[ERROR] 本体论服务 8003 不可用"},
    ]
    fake_llm.responses = [json.dumps([{
        "msg_indices": [0, 1],
        "drawer_topic": "查询失败",
        "summary": "用户问北京消费，但服务不可用",
        "keywords": ["失败"],
        "proposed_room_label": "杂项",
    }])]
    cfg = PalaceConfig()
    w = Writer(cfg=cfg, store=store, llm=fake_llm, embedder=fake_embedder)
    await w.archive_session(session_id="t2", messages=session,
                             now_date="2026-04-16", now_year=2026)
    # 南塔摘要里确实有"不可用"
    drawers = await store.list_room_south_drawers(date="2026-04-16", room=20)
    assert any("不可用" in d["summary"] for d in drawers)
    # 中庭必须是空
    entries = await store.list_atrium_entries()
    assert all("不可用" not in e["content"] for e in entries)
```

- [ ] **Step 3: 实现 Writer**

```python
# mybot/palace/writer.py
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
import logging, re
from datetime import datetime

from .chunker import Chunker, Chunk
from .router import Router, RoomSlot, DrawerSlot
from .store import PalaceStore
from .config import PalaceConfig
from .ids import make_id, Tower


logger = logging.getLogger(__name__)
LLMCallable = Callable[[list[dict[str, Any]]], Awaitable[str]]


EXPLICIT_MARKERS = ["记住", "以后别", "我偏好", "我的 ", "我是", "请记", "不要再"]

EXTRACT_EXPLICIT_PROMPT = """以下是用户消息。识别其中明确要求被长期记住的"规则/偏好/事实"。

用户消息：
---
{msgs}
---

严格输出 JSON 数组，每个元素：
{{"entry_type": "rule|preference|fact", "content": "一句话客观陈述"}}

若无则输出 []。不要加 markdown。"""


@dataclass
class ArchiveResult:
    north_ids: list[str] = field(default_factory=list)
    south_ids: list[str] = field(default_factory=list)
    atrium_ids: list[str] = field(default_factory=list)
    merge_count: int = 0


class Writer:
    def __init__(self, *, cfg: PalaceConfig, store: PalaceStore,
                 llm: LLMCallable, embedder):
        self.cfg = cfg
        self.store = store
        self.llm = llm
        self.embedder = embedder
        self.chunker = Chunker(llm=llm)
        self.router = Router(cfg=cfg, store=store, embedder=embedder, llm=llm)

    async def archive_session(
        self, *, session_id: str, messages: list[dict],
        now_date: str | None = None, now_year: int | None = None,
    ) -> ArchiveResult:
        if not messages:
            return ArchiveResult()
        if now_date is None:
            now_date = datetime.utcnow().strftime("%Y-%m-%d")
        if now_year is None:
            now_year = int(now_date[:4])
        floor = _day_of_year(now_date)

        # 1. chunker
        chunks = await self.chunker.chunk_and_summarise(messages)
        if not chunks:
            logger.info("archive: no chunks extracted for session %s", session_id)
            return ArchiveResult()

        result = ArchiveResult()

        # 2. 每 chunk 做路由 + 写北塔 + 写南塔
        for chunk in chunks:
            slot = await self.router.assign_room(
                date=now_date, proposed_label=chunk.proposed_room_label)
            drawer_slot = await self.router.assign_drawer(date=now_date, slot=slot)

            sub_messages = [messages[i] for i in chunk.msg_indices
                            if 0 <= i < len(messages)]

            if drawer_slot.drawer > 0:
                # 正常路径
                nid = await self.store.insert_north_drawer(
                    year=now_year, floor=floor, room=slot.room,
                    drawer=drawer_slot.drawer, date=now_date,
                    raw_messages=sub_messages,
                )
                emb = self.embedder.encode(chunk.summary)[0]
                sid = await self.store.insert_south_drawer(
                    year=now_year, floor=floor, room=slot.room,
                    drawer=drawer_slot.drawer, date=now_date,
                    north_ref_ids=[nid],
                    room_type=slot.room_type, room_label=slot.room_label,
                    drawer_topic=chunk.drawer_topic,
                    summary=chunk.summary, keywords=chunk.keywords,
                    embedding=emb,
                )
                await self.store.upsert_day_room(
                    date=now_date, room=slot.room,
                    room_type=slot.room_type, room_label=slot.room_label,
                    drawer_count=drawer_slot.drawer,
                )
                result.north_ids.append(nid)
                result.south_ids.append(sid)
            else:
                # 溢出：合并
                # 先开一个"影子"北塔（坐标向末尾挤；这里简化：抽屉号 20，覆盖不大）
                # 语义：北塔应永久可定位 —— 所以依旧插入，但坐标重用 drawer=20
                # 为避免 UNIQUE 冲突，我们改用房间溢出策略：杂项房 20
                misc_slot = RoomSlot(self.cfg.misc_room, "misc", "杂项(溢出)")
                misc_drawer = await self.router.assign_drawer(
                    date=now_date, slot=misc_slot)
                if misc_drawer.drawer < 0:
                    logger.warning("misc room also full; dropping chunk: %s",
                                   chunk.drawer_topic)
                    continue
                nid = await self.store.insert_north_drawer(
                    year=now_year, floor=floor, room=misc_slot.room,
                    drawer=misc_drawer.drawer, date=now_date,
                    raw_messages=sub_messages,
                )
                # 合并入源房间最相似的抽屉
                emb = self.embedder.encode(chunk.summary)[0]
                merge = await self.router.merge_into_existing(
                    date=now_date, slot=slot,
                    new_summary=chunk.summary,
                    new_north_id=nid, new_embedding=emb,
                )
                result.north_ids.append(nid)
                result.merge_count += 1
                await self.store.upsert_day_room(
                    date=now_date, room=misc_slot.room,
                    room_type=misc_slot.room_type, room_label=misc_slot.room_label,
                    drawer_count=misc_drawer.drawer,
                )

        # 3. 显式声明 → 中庭
        await self._maybe_extract_explicit(messages, result)

        return result

    async def _maybe_extract_explicit(
        self, messages: list[dict], result: ArchiveResult,
    ) -> None:
        import json, uuid
        user_msgs = [str(m.get("content", "")) for m in messages
                     if m.get("role") == "user"]
        if not any(any(k in m for k in EXPLICIT_MARKERS) for m in user_msgs):
            return
        prompt = EXTRACT_EXPLICIT_PROMPT.format(msgs="\n".join(user_msgs))
        try:
            raw = await self.llm([{"role": "user", "content": prompt}])
        except Exception as exc:
            logger.warning("explicit extract LLM failed: %s", exc)
            return
        # reuse chunker's JSON parser
        from .chunker import _parse_json_array
        items = _parse_json_array(raw)
        for it in items:
            if not isinstance(it, dict):
                continue
            content = str(it.get("content", "")).strip()
            etype = str(it.get("entry_type", "fact")).strip()
            if not content or etype not in {"rule", "preference", "fact"}:
                continue
            # 黑名单代码层过一遍（触发器兜底）
            if _hits_blacklist(content, self.cfg.guards.blacklist_patterns):
                logger.info("atrium explicit rejected by code blacklist: %s", content[:50])
                continue
            try:
                # fact 类存向量方便未来查
                emb = self.embedder.encode(content)[0] if etype == "fact" else None
                aid = await self.store.insert_atrium_entry(
                    id=str(uuid.uuid4()),
                    entry_type=etype, content=content,
                    source_type="explicit", status="active",
                    confidence=0.95, embedding=emb,
                )
                result.atrium_ids.append(aid)
            except Exception as exc:
                logger.info("atrium insert failed (likely trigger): %s", exc)


def _hits_blacklist(text: str, patterns: list[str]) -> bool:
    return any(p in text for p in patterns)


def _day_of_year(date_str: str) -> int:
    dt = datetime.strptime(date_str, "%Y-%m-%d")
    doy = dt.timetuple().tm_yday
    return 365 if doy == 366 else doy
```

- [ ] **Step 4: 跑测**

```bash
python -m pytest tests/palace/test_writer.py -v
```
Expected: 2 passed

- [ ] **Step 5: Commit**

```bash
git add mybot/palace/writer.py tests/palace/test_writer.py tests/palace/fixtures/beijing_spending_session.json
git commit -m "palace: Writer.archive_session end-to-end"
```

---

## Phase 4 — Read Path

### Task 4.1: Retriever（混合检索 RRF + rerank）

**Files:**
- Create: `mybot/palace/retriever.py`
- Create: `tests/palace/test_retriever.py`

- [ ] **Step 1: 测 RRF + scope 过滤**

```python
# tests/palace/test_retriever.py
import pytest
from mybot.palace.retriever import Retriever, _rrf_merge
from mybot.palace.config import PalaceConfig


def test_rrf_merge_basic():
    a = [{"drawer_id": "A"}, {"drawer_id": "B"}, {"drawer_id": "C"}]
    b = [{"drawer_id": "B"}, {"drawer_id": "D"}, {"drawer_id": "A"}]
    merged = _rrf_merge([a, b], k=60)
    ids = [m["drawer_id"] for m in merged]
    assert ids[0] in {"A", "B"}
    assert set(ids) == {"A", "B", "C", "D"}


class FakeReranker:
    def rerank(self, query, docs):
        # docs containing 'hit' get higher score
        return [1.0 if "hit" in d else 0.1 for d in docs]


@pytest.mark.asyncio
async def test_retriever_end_to_end(store, fake_embedder):
    cfg = PalaceConfig(top_k_south=2)
    texts = ["hit: 北京消费", "上海工作", "hit: 北京吃饭"]
    for i, t in enumerate(texts):
        emb = fake_embedder.encode(t)[0]
        await store.insert_south_drawer(
            year=2026, floor=100+i, room=1, drawer=1,
            date=f"2026-04-{10+i:02d}",
            north_ref_ids=[f"N-2026-{100+i:03d}-01-01"],
            room_type="fixed", room_label="消费",
            drawer_topic=t, summary=t, keywords=["t"],
            embedding=emb,
        )
    retr = Retriever(cfg=cfg, store=store,
                     embedder=fake_embedder, reranker=FakeReranker())
    hits = await retr.search("北京", now_year=2026)
    ids = [h["drawer_id"] for h in hits]
    assert len(ids) == 2
    # rerank 应当把含 'hit' 的顶上去
    for h in hits:
        assert "hit" in h["summary"]
```

- [ ] **Step 2: 实现 Retriever**

```python
# mybot/palace/retriever.py
from __future__ import annotations
from typing import Optional
import logging

from .config import PalaceConfig
from .store import PalaceStore
from .embedder import Embedder
from .reranker import Reranker


logger = logging.getLogger(__name__)


def _rrf_merge(lists: list[list[dict]], k: int = 60) -> list[dict]:
    scores: dict[str, float] = {}
    repr_: dict[str, dict] = {}
    for lst in lists:
        for rank, item in enumerate(lst):
            did = item["drawer_id"]
            scores[did] = scores.get(did, 0.0) + 1.0 / (k + rank + 1)
            repr_.setdefault(did, item)
    merged = sorted(repr_.values(), key=lambda x: -scores[x["drawer_id"]])
    return merged


class Retriever:
    def __init__(self, *, cfg: PalaceConfig, store: PalaceStore,
                 embedder, reranker):
        self.cfg = cfg
        self.store = store
        self.embedder = embedder
        self.reranker = reranker

    async def search(self, query: str, *, now_year: int | None = None,
                     limit: int | None = None) -> list[dict]:
        if not query.strip():
            return []
        limit = limit or self.cfg.top_k_south
        year_min = None
        if now_year is not None:
            year_min = now_year - self.cfg.current_year_scope + 1

        # A: vector
        try:
            q_emb = self.embedder.encode(query)[0]
            vec_hits = await self.store.vec_knn(q_emb, limit=30, year_min=year_min)
        except Exception as exc:
            logger.warning("vector search failed: %s", exc)
            vec_hits = []

        # B: FTS / BM25
        try:
            fts_hits = await self.store.fts_search(query, limit=30)
        except Exception as exc:
            logger.warning("fts search failed: %s", exc)
            fts_hits = []

        merged = _rrf_merge([vec_hits, fts_hits])[:60]
        if not merged:
            return []

        # Hydrate summaries
        hydrated = []
        for h in merged:
            row = await self.store.get_south_drawer(h["drawer_id"])
            if row is None:
                continue
            hydrated.append({**h, **row})

        # Rerank
        try:
            scores = self.reranker.rerank(query, [h["summary"] for h in hydrated])
            for h, s in zip(hydrated, scores):
                h["rerank_score"] = s
            hydrated.sort(key=lambda x: -x.get("rerank_score", 0))
        except Exception as exc:
            logger.warning("rerank failed, using RRF order: %s", exc)

        return hydrated[:limit]
```

- [ ] **Step 3: 跑测**

```bash
python -m pytest tests/palace/test_retriever.py -v
```
Expected: 2 passed

- [ ] **Step 4: Commit**

```bash
git add mybot/palace/retriever.py tests/palace/test_retriever.py
git commit -m "palace: Retriever (vec+FTS RRF+rerank)"
```

---

### Task 4.2: Atrium 注入

**Files:**
- Create: `mybot/palace/atrium.py`
- Create: `tests/palace/test_atrium.py`

- [ ] **Step 1: 测 assemble_atrium_block 格式**

```python
# tests/palace/test_atrium.py
import uuid, pytest
from mybot.palace.atrium import AtriumManager
from mybot.palace.config import PalaceConfig


@pytest.mark.asyncio
async def test_atrium_assemble(store, fake_embedder):
    cfg = PalaceConfig()
    # 插入几个 active 条目
    for etype, c in [("rule", "别启动 myontology/backend"),
                      ("preference", "喜欢简洁直接的回答"),
                      ("fact", "真名邢智强")]:
        await store.insert_atrium_entry(
            id=str(uuid.uuid4()), entry_type=etype, content=c,
            source_type="explicit", status="active",
            embedding=fake_embedder.encode(c)[0] if etype == "fact" else None,
        )
    mgr = AtriumManager(cfg=cfg, store=store, embedder=fake_embedder)
    block = await mgr.assemble_block(query="身份 / 规则 / 偏好", now_year=2026)
    assert "[规则]" in block
    assert "myontology/backend" in block
    assert "[偏好]" in block
    assert "[事实]" in block
```

- [ ] **Step 2: 实现**

```python
# mybot/palace/atrium.py
from __future__ import annotations
import logging
from typing import Optional
from .config import PalaceConfig
from .store import PalaceStore


logger = logging.getLogger(__name__)


TYPE_LABEL = {"rule": "规则", "preference": "偏好", "fact": "事实"}


class AtriumManager:
    def __init__(self, *, cfg: PalaceConfig, store: PalaceStore, embedder):
        self.cfg = cfg
        self.store = store
        self.embedder = embedder

    async def assemble_block(self, *, query: str, now_year: int) -> str:
        entries = await self.store.list_atrium_entries(status="active")
        rules = [e for e in entries if e["entry_type"] == "rule"]
        prefs = [e for e in entries if e["entry_type"] == "preference"]
        facts = [e for e in entries if e["entry_type"] == "fact"]

        # facts: top N by vector similarity
        if facts and query.strip():
            try:
                import numpy as np
                q_emb = self.embedder.encode(query)[0]
                scored = []
                for f in facts:
                    emb = await self.store.get_atrium_embedding(f["id"])
                    if emb is None:
                        continue
                    scored.append((float(np.dot(q_emb, emb)), f))
                scored.sort(key=lambda x: -x[0])
                facts = [f for _, f in scored[:self.cfg.top_k_fact]]
            except Exception as exc:
                logger.warning("fact vec rank failed: %s", exc)
                facts = facts[:self.cfg.top_k_fact]

        if not (rules or prefs or facts):
            return ""

        lines = ["## 🏛️ 用户规则与偏好（中庭·永久）"]
        for e in rules:
            lines.append(f"- [{TYPE_LABEL['rule']}] {e['content']}")
        for e in prefs:
            lines.append(f"- [{TYPE_LABEL['preference']}] {e['content']}")
        for e in facts:
            lines.append(f"- [{TYPE_LABEL['fact']}] {e['content']}")
        return "\n".join(lines)
```

在 `store.py` 加 `get_atrium_embedding`:
```python
    async def get_atrium_embedding(self, entry_id: str):
        import numpy as np
        async with self.acquire() as conn:
            row = await (await conn.execute(
                "SELECT embedding FROM atrium_vec WHERE entry_id=?", (entry_id,)
            )).fetchone()
        if row is None:
            return None
        return np.frombuffer(row[0], dtype="float32")
```

- [ ] **Step 3: 跑测**

```bash
python -m pytest tests/palace/test_atrium.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add mybot/palace/atrium.py mybot/palace/store.py tests/palace/test_atrium.py
git commit -m "palace: Atrium injection block"
```

---

## Phase 5 — MemoryPalace 门面 & Agent 集成

### Task 5.1: MemoryPalace facade (`__init__.py`)

**Files:**
- Modify: `mybot/palace/__init__.py`
- Create: `tests/palace/test_palace_facade.py`

- [ ] **Step 1: 测 assemble_context / archive_session 面向 agent 的接口**

```python
# tests/palace/test_palace_facade.py
import json, pytest
from pathlib import Path
from mybot.palace import MemoryPalace
from mybot.palace.config import PalaceConfig


@pytest.mark.asyncio
async def test_palace_round_trip(tmp_path, fake_llm, fake_embedder):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    session = json.loads(Path("tests/palace/fixtures/beijing_spending_session.json").read_text())
    fake_llm.responses = [json.dumps([{
        "msg_indices": [0, 1, 2], "drawer_topic": "北京消费",
        "summary": "用户问北京消费 69 万；要求别启动 myontology/backend",
        "keywords": ["北京", "消费"], "proposed_room_label": "消费"
    }])]

    class _Reranker:
        def rerank(self, q, docs): return [1.0]*len(docs)

    palace = MemoryPalace(cfg=cfg, llm=fake_llm,
                           embedder=fake_embedder, reranker=_Reranker())
    await palace.initialize()
    await palace.archive_session("s1", session,
                                   now_date="2026-04-16", now_year=2026)
    ctx = await palace.assemble_context("北京花了多少", now_year=2026,
                                         now_date="2026-04-16")
    assert "北京" in ctx
    assert "myontology/backend" in ctx  # atrium rule block
    assert "不可用" not in ctx  # 防铁锈
```

- [ ] **Step 2: 实现 facade**

```python
# mybot/palace/__init__.py
"""MyBot 丽泽园记忆系统门面。"""
from __future__ import annotations
import logging
from datetime import datetime
from typing import Any, Awaitable, Callable, Optional

from .config import PalaceConfig
from .store import PalaceStore
from .writer import Writer, ArchiveResult
from .retriever import Retriever
from .atrium import AtriumManager


__version__ = "0.1.0"
logger = logging.getLogger(__name__)
LLMCallable = Callable[[list[dict[str, Any]]], Awaitable[str]]


class MemoryPalace:
    """The facade mybot/agent.py talks to.

    Signature-compatible with old MemoryEngine on `get_context_for_prompt`
    and `end_session`.
    """

    def __init__(self, *, cfg: PalaceConfig, llm: LLMCallable,
                 embedder, reranker):
        self.cfg = cfg
        self.store = PalaceStore(cfg)
        self.writer = Writer(cfg=cfg, store=self.store, llm=llm, embedder=embedder)
        self.retriever = Retriever(cfg=cfg, store=self.store,
                                    embedder=embedder, reranker=reranker)
        self.atrium = AtriumManager(cfg=cfg, store=self.store, embedder=embedder)
        self._initialized = False

    async def initialize(self) -> None:
        if self._initialized:
            return
        await self.store.initialize()
        self._initialized = True
        logger.info("MemoryPalace initialized: %s", self.cfg.db_path)

    async def assemble_context(self, user_query: str, *,
                                now_year: int | None = None,
                                now_date: str | None = None) -> str:
        if not self._initialized:
            await self.initialize()
        now_year = now_year or datetime.utcnow().year
        atrium_block = await self.atrium.assemble_block(query=user_query,
                                                          now_year=now_year)
        south_hits = await self.retriever.search(user_query, now_year=now_year)

        parts = []
        if atrium_block:
            parts.append(atrium_block)
        if south_hits:
            parts.append("## 📚 可能相关的过去对话（南塔·top 5）")
            for i, h in enumerate(south_hits, 1):
                parts.append(
                    f"{i}. [{h['date']} {h['room_label']}/{h['drawer_topic']}]"
                    f"（坐标 {h['id']}，可 get_raw_conversation 取原文）\n"
                    f"   {h['summary']}"
                )
        return "\n\n".join(parts)

    async def archive_session(self, session_id: str, messages: list[dict],
                               *, now_date: str | None = None,
                               now_year: int | None = None) -> ArchiveResult:
        if not self._initialized:
            await self.initialize()
        return await self.writer.archive_session(
            session_id=session_id, messages=messages,
            now_date=now_date, now_year=now_year)

    # --- MemoryEngine 兼容 shim ---
    async def get_context_for_prompt(self, query: str) -> str:
        return await self.assemble_context(query)

    async def end_session(self, session_id: str, conversation_messages: list[dict]) -> dict:
        result = await self.archive_session(session_id, conversation_messages)
        return {
            "session_id": session_id,
            "north_ids": result.north_ids,
            "south_ids": result.south_ids,
            "atrium_ids": result.atrium_ids,
            "merge_count": result.merge_count,
        }

    async def get_stats(self) -> dict:
        if not self._initialized:
            await self.initialize()
        async with self.store.acquire() as conn:
            async def cnt(sql, params=()):
                return (await (await conn.execute(sql, params)).fetchone())[0]
            return {
                "north_drawers": await cnt("SELECT COUNT(*) FROM north_drawer"),
                "south_drawers": await cnt("SELECT COUNT(*) FROM south_drawer"),
                "atrium_active": await cnt(
                    "SELECT COUNT(*) FROM atrium_entry WHERE status='active'"),
                "atrium_pending": await cnt(
                    "SELECT COUNT(*) FROM atrium_entry WHERE status='pending'"),
            }
```

- [ ] **Step 3: 跑测**

```bash
python -m pytest tests/palace/test_palace_facade.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add mybot/palace/__init__.py tests/palace/test_palace_facade.py
git commit -m "palace: MemoryPalace facade with engine-compatible shims"
```

---

### Task 5.2: Agent 集成

**Files:**
- Modify: `mybot/agent.py`
- Modify: `mybot/__main__.py`（实例化 palace 替代 memory_engine）
- Create: `tests/palace/test_agent_integration.py`（smoke）

- [ ] **Step 1: 先看 `mybot/__main__.py` 怎么实例化 MemoryEngine**

```bash
grep -n "MemoryEngine\|memory_engine" mybot/__main__.py mybot/agent.py
```

- [ ] **Step 2: 在 `__main__.py` 把 MemoryEngine 实例化替换为 MemoryPalace**

原来类似：
```python
memory_engine = MemoryEngine(db_path=cfg.memory.db_path, llm_callable=llm_call)
agent = Agent(..., memory_engine=memory_engine, ...)
```

改为：
```python
from mybot.palace import MemoryPalace
from mybot.palace.config import PalaceConfig
from mybot.palace.embedder import Embedder
from mybot.palace.reranker import Reranker

palace_cfg = PalaceConfig.from_dict(cfg_dict)
embedder = Embedder(model_name=palace_cfg.embedder, dim=palace_cfg.embedder_dim)
reranker = Reranker(model_name=palace_cfg.reranker)
palace = MemoryPalace(cfg=palace_cfg, llm=llm_call,
                       embedder=embedder, reranker=reranker)
await palace.initialize()
agent = Agent(..., memory_engine=palace, ...)
```

（保持 `memory_engine=` 参数名，不改 Agent；MemoryPalace 已有 shim 方法）

- [ ] **Step 3: smoke 测试：mybot 能导入 + 初始化 palace 不报错**

```python
# tests/palace/test_agent_integration.py
def test_import_chain():
    # 全路径能 import
    import mybot.palace
    from mybot.palace import MemoryPalace
    from mybot.palace.config import PalaceConfig
    assert MemoryPalace is not None
    assert PalaceConfig is not None
```

```bash
python -m pytest tests/palace/test_agent_integration.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
git add mybot/__main__.py tests/palace/test_agent_integration.py
git commit -m "palace: wire MemoryPalace into mybot __main__"
```

---

## Phase 6 — Tool: palace

### Task 6.1: palace BaseTool

**Files:**
- Create: `mybot/palace/tool_palace.py`
- Modify: `mybot/tools/__init__.py`（注册工具）
- Modify: `config.yaml`（`tools.enabled` 追加 `palace`；移除 `memory`）
- Create: `tests/palace/test_tool_palace.py`

- [ ] **Step 1: 测 get_raw_conversation 回原文**

```python
# tests/palace/test_tool_palace.py
import json, pytest
from mybot.palace.tool_palace import PalaceTool
from mybot.palace import MemoryPalace
from mybot.palace.config import PalaceConfig


@pytest.mark.asyncio
async def test_tool_get_raw_conversation(tmp_path, fake_llm, fake_embedder):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    fake_llm.responses = [json.dumps([{
        "msg_indices": [0, 1], "drawer_topic": "测试",
        "summary": "测试摘要", "keywords": [], "proposed_room_label": "消费"
    }])]
    class R:
        def rerank(self, q, d): return [1.0]*len(d)
    palace = MemoryPalace(cfg=cfg, llm=fake_llm, embedder=fake_embedder, reranker=R())
    await palace.initialize()
    msgs = [{"role":"user","content":"hi"}, {"role":"assistant","content":"hello"}]
    result = await palace.archive_session("s1", msgs,
                                            now_date="2026-04-16", now_year=2026)
    nid = result.north_ids[0]

    tool = PalaceTool(palace=palace)
    tr = await tool.execute(operation="get_raw_conversation", drawer_id=nid)
    assert tr.success
    data = json.loads(tr.output)
    assert data["raw_messages"][0]["content"] == "hi"
```

- [ ] **Step 2: 实现**

```python
# mybot/palace/tool_palace.py
from __future__ import annotations
import json
from mybot.tools.base import BaseTool, ToolResult


class PalaceTool(BaseTool):
    name = "palace"
    description = "查丽泽园记忆系统。按坐标取原文、列某天的抽屉、看中庭条目"
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["get_raw_conversation", "list_day_drawers",
                         "list_atrium", "show_atrium_entry", "stats"],
            },
            "drawer_id": {"type": "string"},
            "date": {"type": "string", "description": "YYYY-MM-DD"},
            "entry_id": {"type": "string"},
            "entry_type": {"type": "string",
                           "enum": ["rule", "preference", "fact"]},
        },
        "required": ["operation"],
    }

    def __init__(self, palace):
        self.palace = palace

    async def execute(self, **params) -> ToolResult:
        op = params.get("operation")
        try:
            if op == "get_raw_conversation":
                drawer_id = params["drawer_id"]
                if drawer_id.startswith("S-"):
                    s = await self.palace.store.get_south_drawer(drawer_id)
                    if not s:
                        return ToolResult(success=False, output="",
                                           error=f"no south drawer {drawer_id}")
                    north_ids = s["north_ref_ids"]
                    convos = [await self.palace.store.get_north_drawer(nid)
                              for nid in north_ids]
                    return ToolResult(success=True,
                                       output=json.dumps(
                                           {"south": s, "north_messages": convos},
                                           ensure_ascii=False))
                else:
                    n = await self.palace.store.get_north_drawer(drawer_id)
                    if not n:
                        return ToolResult(success=False, output="",
                                           error=f"no north drawer {drawer_id}")
                    return ToolResult(success=True,
                                       output=json.dumps(n, ensure_ascii=False))
            elif op == "list_day_drawers":
                date = params["date"]
                rooms = await self.palace.store.get_day_room_map(date)
                return ToolResult(success=True,
                                   output=json.dumps(rooms, ensure_ascii=False))
            elif op == "list_atrium":
                etype = params.get("entry_type")
                entries = await self.palace.store.list_atrium_entries(
                    status="active", entry_type=etype)
                return ToolResult(success=True,
                                   output=json.dumps(entries, ensure_ascii=False,
                                                     default=str))
            elif op == "show_atrium_entry":
                e = await self.palace.store.get_atrium_entry(params["entry_id"])
                if not e:
                    return ToolResult(success=False, output="",
                                       error=f"no entry {params['entry_id']}")
                return ToolResult(success=True,
                                   output=json.dumps(e, ensure_ascii=False, default=str))
            elif op == "stats":
                s = await self.palace.get_stats()
                return ToolResult(success=True, output=json.dumps(s, ensure_ascii=False))
            else:
                return ToolResult(success=False, output="", error=f"unknown op {op}")
        except Exception as exc:
            return ToolResult(success=False, output="", error=str(exc))
```

- [ ] **Step 3: 在 `mybot/tools/__init__.py` 注册 PalaceTool**

加载时把 `palace` 名字 → `PalaceTool(palace_instance)`。注意需要 palace 实例注入。参考现有 `memory_tool` 的注册方式。

- [ ] **Step 4: config.yaml 改 tools.enabled**

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
    - palace
```
（去掉 memory，加 palace）

- [ ] **Step 5: 跑测**

```bash
python -m pytest tests/palace/test_tool_palace.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add mybot/palace/tool_palace.py mybot/tools/__init__.py config.yaml tests/palace/test_tool_palace.py
git commit -m "palace: add palace BaseTool + register"
```

---

## Phase 7 — CLI

### Task 7.1: cli init/stats/list/show

**Files:**
- Create: `mybot/palace/cli.py`
- Modify: `mybot/__main__.py`（加 `memory` subcommand 分发）
- Create: `tests/palace/test_cli.py`

- [ ] **Step 1: 测 init 能创建库 + stats 返回计数**

```python
# tests/palace/test_cli.py
import pytest
from mybot.palace.cli import cmd_init, cmd_stats
from mybot.palace.config import PalaceConfig


@pytest.mark.asyncio
async def test_cli_init_and_stats(tmp_path, capsys):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    await cmd_init(cfg)
    assert cfg.db_path.exists()
    await cmd_stats(cfg)
    out = capsys.readouterr().out
    assert "north_drawers" in out
```

- [ ] **Step 2: 实现 cli.py（分 init / stats / list / show / review / edit / archive / resurrect / audit / backup）**

```python
# mybot/palace/cli.py
"""CLI: python -m mybot memory <subcommand>"""
from __future__ import annotations
import argparse, asyncio, json, shutil, uuid
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import PalaceConfig
from .store import PalaceStore


async def cmd_init(cfg: PalaceConfig) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    print(f"palace.db initialized at {cfg.db_path}")


async def cmd_stats(cfg: PalaceConfig) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    async with store.acquire() as conn:
        async def cnt(sql):
            return (await (await conn.execute(sql)).fetchone())[0]
        stats = {
            "north_drawers": await cnt("SELECT COUNT(*) FROM north_drawer"),
            "south_drawers": await cnt("SELECT COUNT(*) FROM south_drawer"),
            "atrium_total": await cnt("SELECT COUNT(*) FROM atrium_entry"),
            "atrium_active": await cnt(
                "SELECT COUNT(*) FROM atrium_entry WHERE status='active'"),
            "atrium_pending": await cnt(
                "SELECT COUNT(*) FROM atrium_entry WHERE status='pending'"),
            "atrium_rejected": await cnt(
                "SELECT COUNT(*) FROM atrium_entry WHERE status='rejected'"),
            "atrium_archived": await cnt(
                "SELECT COUNT(*) FROM atrium_entry WHERE status='archived'"),
        }
    print(json.dumps(stats, indent=2, ensure_ascii=False))


async def cmd_list(cfg: PalaceConfig, *, status: str | None,
                    entry_type: str | None) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    entries = await store.list_atrium_entries(status=status, entry_type=entry_type)
    if not entries:
        print("(empty)")
        return
    for e in entries:
        print(f"- [{e['entry_type']:10s}] [{e['status']:8s}] {e['id'][:8]}  {e['content'][:80]}")


async def cmd_show(cfg: PalaceConfig, entry_id: str) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    e = await store.get_atrium_entry(entry_id)
    if not e:
        print(f"no entry {entry_id}")
        return
    print(json.dumps(e, indent=2, ensure_ascii=False, default=str))


async def cmd_review(cfg: PalaceConfig) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    pending = await store.list_atrium_entries(status="pending")
    if not pending:
        print("no pending entries.")
        return
    for i, e in enumerate(pending, 1):
        print(f"\n[{i}/{len(pending)}] pending {e['source_type']} {e['entry_type']}")
        print(f"  id:      {e['id']}")
        print(f"  content: {e['content']}")
        print(f"  evidence ({e['evidence_count']} drawers): "
              + ", ".join(e['evidence_drawer_ids'][:5]))
        while True:
            choice = input("  [a]pprove / [r]eject / [e]dit / [s]kip: ").strip().lower()
            if choice in {"a", "approve"}:
                await store.update_atrium_status(e["id"], "active", actor="user_cli")
                print("  → active")
                break
            elif choice in {"r", "reject"}:
                await store.update_atrium_status(e["id"], "rejected", actor="user_cli")
                print("  → rejected")
                break
            elif choice in {"e", "edit"}:
                new = input("    edited content: ").strip()
                async with store.acquire() as conn:
                    await conn.execute(
                        "UPDATE atrium_entry SET content=? WHERE id=?",
                        (new, e["id"]),
                    )
                    await conn.commit()
                await store.update_atrium_status(e["id"], "active", actor="user_cli")
                print("  → edited + active")
                break
            elif choice in {"s", "skip", ""}:
                print("  → skipped")
                break


async def cmd_archive(cfg: PalaceConfig, entry_id: str) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    await store.update_atrium_status(entry_id, "archived", actor="user_cli")
    print(f"archived {entry_id}")


async def cmd_resurrect(cfg: PalaceConfig, entry_id: str) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    await store.update_atrium_status(entry_id, "active", actor="user_cli")
    print(f"resurrected {entry_id}")


async def cmd_backup(cfg: PalaceConfig) -> None:
    ts = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    dst = cfg.db_path.parent / f"palace.db.bak-{ts}"
    shutil.copy2(cfg.db_path, dst)
    print(f"backup → {dst}")


def _load_cfg() -> PalaceConfig:
    import yaml
    p = Path("config.yaml")
    if not p.exists():
        return PalaceConfig()
    return PalaceConfig.from_dict(yaml.safe_load(p.read_text()))


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mybot memory")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("stats")
    lp = sub.add_parser("list")
    lp.add_argument("--status")
    lp.add_argument("--type", dest="entry_type")
    sp = sub.add_parser("show"); sp.add_argument("entry_id")
    sub.add_parser("review")
    ap = sub.add_parser("archive"); ap.add_argument("entry_id")
    rp = sub.add_parser("resurrect"); rp.add_argument("entry_id")
    sub.add_parser("backup")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = _load_cfg()

    async def _run():
        if args.cmd == "init":
            await cmd_init(cfg)
        elif args.cmd == "stats":
            await cmd_stats(cfg)
        elif args.cmd == "list":
            await cmd_list(cfg, status=args.status, entry_type=args.entry_type)
        elif args.cmd == "show":
            await cmd_show(cfg, args.entry_id)
        elif args.cmd == "review":
            await cmd_review(cfg)
        elif args.cmd == "archive":
            await cmd_archive(cfg, args.entry_id)
        elif args.cmd == "resurrect":
            await cmd_resurrect(cfg, args.entry_id)
        elif args.cmd == "backup":
            await cmd_backup(cfg)

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 3: 在 `mybot/__main__.py` 加 memory subcommand 分发**

在 __main__ 的入口 main() 里，检测 `sys.argv[1] == "memory"` → 调用 `mybot.palace.cli.main(sys.argv[2:])`。

```python
# 顶层 main 函数改造示例
def main():
    import sys
    if len(sys.argv) > 1 and sys.argv[1] == "memory":
        from mybot.palace.cli import main as palace_cli_main
        return palace_cli_main(sys.argv[2:])
    # ... 原有 agent 启动逻辑 ...
```

- [ ] **Step 4: 跑测 + 手测**

```bash
python -m pytest tests/palace/test_cli.py -v
python -m mybot memory init
python -m mybot memory stats
```
Expected: 全 PASS / 两个命令返回正常 JSON。

- [ ] **Step 5: Commit**

```bash
git add mybot/palace/cli.py mybot/__main__.py tests/palace/test_cli.py
git commit -m "palace: CLI init/stats/list/show/review/archive/resurrect/backup"
```

---

## Phase 8 — 防铁锈 E2E（最重要）

### Task 8.1: 防铁锈专项测试

**Files:**
- Create: `tests/palace/test_no_rust.py`
- Create: `tests/palace/fixtures/tool_failure_session.json`

- [ ] **Step 1: 建 fixture：复现今天早上的失败 session**

```json
[
  {"role": "user", "content": "我在北京花了多少钱"},
  {"role": "assistant", "content": "[调用 ontology 工具失败: 本体论服务端口 8003 不可用]"},
  {"role": "assistant", "content": "抱歉，系统未能找到与北京消费相关的信息。"}
]
```

- [ ] **Step 2: 测：归档后再问相同问题，返回不能出现"不可用"**

```python
# tests/palace/test_no_rust.py
import json, pytest
from pathlib import Path
from mybot.palace import MemoryPalace
from mybot.palace.config import PalaceConfig


@pytest.mark.asyncio
async def test_failure_session_does_not_pollute_atrium(
    tmp_path, fake_llm, fake_embedder,
):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    session = json.loads(Path("tests/palace/fixtures/tool_failure_session.json").read_text())

    # Chunker may summarise the failure into south — that's fine
    fake_llm.responses = [json.dumps([{
        "msg_indices": [0, 1, 2], "drawer_topic": "查询失败",
        "summary": "用户问北京消费，工具报错，未能返回结果",
        "keywords": ["北京", "消费", "失败"],
        "proposed_room_label": "消费",
    }])]

    class R:
        def rerank(self, q, d): return [1.0]*len(d)
    palace = MemoryPalace(cfg=cfg, llm=fake_llm, embedder=fake_embedder, reranker=R())
    await palace.initialize()
    await palace.archive_session("fail-morning", session,
                                   now_date="2026-04-16", now_year=2026)

    # 中庭必须是空（因为没有显式"记住"）
    entries = await palace.store.list_atrium_entries()
    assert len(entries) == 0, f"atrium should be empty, got {entries}"

    # 再问 → 组合 prompt 里不能出现让 LLM 复读的错误
    ctx = await palace.assemble_context(
        "我在北京花了多少钱", now_year=2026, now_date="2026-04-16")
    # 南塔摘要可以出现（因为是事实摘要），但中庭块必须没有
    assert "🏛️" not in ctx  # atrium block was NOT emitted
    # 南塔摘要里可以有"失败"，但断言：没有被当成规则注入
    assert "[规则]" not in ctx
    assert "[偏好]" not in ctx
    assert "[事实]" not in ctx


@pytest.mark.asyncio
async def test_blacklist_blocks_explicit_attempt(tmp_path, fake_llm, fake_embedder):
    """即使用户显式"记住 XX 不可用"，也不准进中庭。"""
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    session = [
        {"role": "user", "content": "记住：端口 8003 不可用"}
    ]
    fake_llm.responses = [
        json.dumps([]),  # chunker 返回空
        json.dumps([{"entry_type": "rule", "content": "端口 8003 不可用"}]),  # explicit extract
    ]
    class R:
        def rerank(self, q, d): return []
    palace = MemoryPalace(cfg=cfg, llm=fake_llm, embedder=fake_embedder, reranker=R())
    await palace.initialize()
    await palace.archive_session("s", session,
                                   now_date="2026-04-16", now_year=2026)
    entries = await palace.store.list_atrium_entries()
    assert len(entries) == 0  # 代码层或触发器都拦下来
```

- [ ] **Step 3: 跑**

```bash
python -m pytest tests/palace/test_no_rust.py -v
```
Expected: 2 PASS

- [ ] **Step 4: Commit**

```bash
git add tests/palace/test_no_rust.py tests/palace/fixtures/tool_failure_session.json
git commit -m "palace: anti-rust E2E (failure sessions do not pollute atrium)"
```

---

## Phase 9 — 一次跑全 + 打包

### Task 9.1: 全量测试 + 统计

- [ ] **Step 1: 跑全量测试**

```bash
python -m pytest tests/palace/ -v -m 'not slow' --tb=short
```
Expected: 全绿。若有 fail 回溯修。

- [ ] **Step 2: 生成覆盖率（可选）**

```bash
pip install pytest-cov
python -m pytest tests/palace/ --cov=mybot/palace --cov-report=term-missing
```

- [ ] **Step 3: Commit（如果前面修 bug 没 commit）**

---

## Phase 10 — 迁移 + 灰度

### Task 10.1: 数据迁移 + launchd 重启

**Files:**
- 无新文件

- [ ] **Step 1: 备份旧 memory.db**

```bash
cp /Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/data/memory.db \
   /Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/data/memory.db.legacy-20260416.bak
```

- [ ] **Step 2: 初始化 palace.db**

```bash
cd /Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot
python -m mybot memory init
python -m mybot memory stats
```
Expected: stats 返回全 0。

- [ ] **Step 3: 重启 mybot launchd**

```bash
launchctl kickstart -k gui/$(id -u)/com.xingzq.mybot
sleep 5
tail -30 /Users/ddn/Developer/02_AI_Assistants/Claude_Work/mybot/data/mybot.out.log
```
Expected: 日志显示 `MemoryPalace initialized`；无 error。

- [ ] **Step 4: Telegram smoke**

在 Telegram 发：
1. "你记得我是谁吗" — 预期：不记得（中庭空）/ 基于对话常识回答
2. "记住我偏好简洁回答" — 预期：回复确认
3. 等 session 结束（10 分钟 或 /end）
4. `python -m mybot memory list --status active` — 预期：出现"简洁回答"条目

- [ ] **Step 5: 提交 session 笔记**

创建 `docs/sessions/2026-04-16-palace-rollout.md`：
- 列明迁移时间、备份文件路径、上线前后 stats、Telegram smoke 结果、剩余 v0.2 TODO
- commit

```bash
git add data/memory.db.legacy-20260416.bak  # 或加到 .gitignore
git add docs/sessions/2026-04-16-palace-rollout.md
git commit -m "palace: v0.1 rolled out, legacy memory.db archived"
```

---

## 延后 v0.2（不做）

下列 Task 标记为 v0.2，今晚不做，在 session 笔记里记清楚：

- `mybot/palace/inspector.py` — 30 天巡检 daemon（launchd cron）
- `mybot/palace/proposer.py` — inferred 候选 nightly
- 冲突检测（原 Task 中庭 §6.4）—— 先不上，等首批真实数据再说
- 每周 Telegram 汇总推送
- iCloud 归档
- 年份塔自动换年脚本（2026 过渡 2027 时再写）
- Year-tower 可视化 UI

---

## 全局自审清单

执行前最后过一遍：

- [ ] Phase 0 的 sqlite-vec 版本 API 是否正确（`sqlite_vec.load()` / `loadable_path()` — 两个库都有，别混用）
- [ ] 所有 async fixture 用 `pytest_asyncio.fixture`，不是普通 `pytest.fixture`
- [ ] `insert_south_drawer` 的 embedding 字段必须转 bytes；不要直接传 np.ndarray
- [ ] FTS5 tokenize=unicode61 —— 中文粒度一般；若日后要更准确可换 `jieba` 外部 tokenizer（v0.2）
- [ ] Router 的 `_fixed_label_to_room` 是 label→room，chunker 输出的 `proposed_room_label` 必须跟配置里的固定 10 类**字符串严格相等**才命中（"消费" vs "消费相关" 是两个值）
- [ ] Writer 溢出路径：当前实现把北塔也挤进杂项房 20，语义上可接受但 spec 里略有不同 —— rollout 笔记要声明
- [ ] `mybot/__main__.py` 改动前务必读原有初始化代码，别破坏既有参数传递
- [ ] Agent shim：`MemoryEngine.end_session` 返回值是 dict，`MemoryPalace.end_session` 也要返回 dict（已在 facade 实现）
- [ ] git 不要把 `data/palace.db` 提交（加 .gitignore）
- [ ] bge-m3 和 reranker 的下载要在 `pip install` 后**立即后台启动**，不要等到 rollout 时才下载（~3GB / 10+ 分钟）
- [ ] config.yaml 的 `memory` 段保留还是删除？—— 保留但注释"legacy, 不再读"。`tools.enabled` 移除 `memory`。

---

**计划完。** 按 Task 顺序 TDD 推进，每步 commit，鬼知道哪一步栽。Phase 0 先把依赖和基础装好；Phase 1-4 是主干；Phase 5-6 是 agent 集成；Phase 7 是运维 CLI；Phase 8 是今晚最重要的验证（防铁锈）；Phase 10 才动线上。

今晚预估：Phase 0-2（~90 min） + Phase 3-4（~120 min） + Phase 5-7（~90 min） + Phase 8-10（~60 min） ≈ 6 小时。如果模型下载慢或撞坑，延到明早；Phase 5+ 可视情况截断在集成 smoke 处过夜。
