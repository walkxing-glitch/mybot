# soho-twin-towers：丽泽SOHO双塔DNA记忆系统独立服务

> 设计日期：2026-04-18

## 1 背景

mybot 内嵌的 `mybot/palace/` 模块实现了完整的记忆宫殿系统（北塔原始对话 + 南塔摘要向量 + 中庭永久规则）。为与 myontology、neural-twin 保持统一架构——mybot 纯编排层，各引擎独立 HTTP 服务——将 Palace 拆为独立项目 `soho-twin-towers`。

## 2 设计决策

| 决策 | 选择 | 理由 |
|------|------|------|
| 项目名/目录名 | `soho-twin-towers` | 保留 SOHO 双塔品牌 |
| 端口 | 8004 | myontology 8003，顺延 |
| LLM | 服务自带 litellm client | 自治，不依赖 mybot |
| 数据库 | 继续 SQLite + sqlite-vec | 单用户够用，sqlite-vec 已跑通 |
| Embedder | 豆包 API (2048-dim) | 不依赖本地 torch |
| Reranker | 默认 none (NullReranker) | 2017 MBP 跑本地模型太重 |
| mybot 侧 | 彻底删除 palace/，换 HTTP client | 跟 ontology tool 风格一致 |
| 迁移策略 | 一步到位 | 单用户，无灰度必要 |

## 3 项目结构

```
soho-twin-towers/
├── gateway/
│   ├── main.py              # FastAPI app, port 8004
│   ├── routes/
│   │   ├── session.py       # context + archive
│   │   ├── atrium.py        # 中庭 CRUD
│   │   ├── drawers.py       # 北塔/南塔查询
│   │   └── stats.py         # 统计
│   └── llm.py               # litellm wrapper
├── palace/
│   ├── __init__.py           # MemoryPalace 门面
│   ├── writer.py             # archive_session + 防锈三道闸
│   ├── chunker.py            # LLM 对话切分摘要
│   ├── router.py             # 房间/抽屉分配
│   ├── store.py              # apsw + sqlite-vec 存储层
│   ├── atrium.py             # 中庭管理器
│   ├── retriever.py          # 南塔检索（向量 + BM25 + RRF）
│   ├── config.py             # PalaceConfig + AtriumGuards
│   ├── ids.py                # ID 生成（N-/S- 坐标）
│   ├── embedder_doubao.py    # 豆包 API embedder
│   ├── embedder.py           # 本地 BAAI embedder（备用）
│   ├── reranker.py           # 本地 reranker（备用）
│   └── migrations/
│       └── 001_init.sql
├── tests/                    # 从 mybot/tests/palace/ 迁入
│   ├── conftest.py
│   ├── fixtures/
│   │   ├── tool_failure_session.json
│   │   ├── beijing_spending_session.json
│   │   └── multi_topic_session.json
│   ├── test_no_rust.py       # 防锈回归测试
│   ├── test_atrium_guards.py
│   ├── test_store.py
│   ├── test_writer.py
│   ├── test_chunker.py
│   ├── test_retriever.py
│   ├── test_router.py
│   └── ...                   # 其余测试文件
├── data/
│   └── palace.db             # 从 mybot/data/palace.db 复制
├── config.yaml
├── pyproject.toml
├── CLAUDE.md
└── README.md
```

## 4 API 设计

Base URL: `http://localhost:8004`

### 4.1 会话接口

#### `POST /session/context`

每轮对话前获取记忆上下文，注入 LLM prompt。

```json
// Request
{"query": "我在北京花了多少钱"}

// Response
{"context": "## 🏛️ 用户规则与偏好（中庭·永久）\n- [偏好] 以后别加太多emoji\n\n## 📚 可能相关的过去对话（南塔·top 5）\n..."}
```

#### `POST /session/archive`

每轮对话后归档消息。触发：北塔写入、南塔摘要、中庭显式提取（含防锈三道闸）。

```json
// Request
{
  "session_id": "tg-12345-1713420000",
  "messages": [
    {"role": "user", "content": "记住：以后别加太多emoji"},
    {"role": "assistant", "content": "好的，已记住。"}
  ]
}

// Response
{
  "session_id": "tg-12345-1713420000",
  "north_ids": ["N-2026-108-06-03"],
  "south_ids": ["S-2026-108-06-03"],
  "atrium_ids": ["a1b2c3d4"],
  "merge_count": 0
}
```

### 4.2 中庭接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/atrium?status=active&entry_type=rule` | 列条目（可选过滤） |
| `GET` | `/atrium/{id}` | 查单条 |

### 4.3 抽屉接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/drawers/{date}` | 列某天所有房间/抽屉 |
| `GET` | `/drawers/{id}/raw` | 取北塔原始对话或南塔摘要 |

### 4.4 统计接口

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/stats` | 返回 north_drawers / south_drawers / atrium_active / atrium_pending 计数 |

## 5 配置

`config.yaml`:

```yaml
llm:
  model: "deepseek/deepseek-chat"
  api_key: "${DEEPSEEK_API_KEY}"

embedder:
  provider: "doubao"
  dim: 2048
  api_key: "${DOUBAO_API_KEY}"

palace:
  db_path: "data/palace.db"
  reranker: "none"
  enabled: true
  top_k_south: 5
  top_k_fact: 3
  fixed_rooms:
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

## 6 mybot 侧改动

### 6.1 新增

`mybot/tools/palace.py` — HTTP client，兼容 `memory_engine` 接口：

```python
class PalaceClient:
    """HTTP client for soho-twin-towers, compatible with memory_engine interface."""
    
    def __init__(self, base_url: str = "http://localhost:8004"):
        self.base_url = base_url

    async def get_context_for_prompt(self, query: str) -> str:
        # POST /session/context
        
    async def end_session(self, session_id: str, conversation_messages: list) -> dict:
        # POST /session/archive
        
    async def get_stats(self) -> dict:
        # GET /stats
```

`PalaceTool`（agent 工具）复用同一个 client，暴露 drawers/atrium/stats 操作给 LLM。

### 6.2 删除

- `mybot/palace/` 整个目录（14 .py + 1 .sql）
- `tests/palace/` 整个目录（17 测试文件 + 3 fixtures）

### 6.3 修改

- `mybot/gateway/cli.py` — `_try_build_palace()` 改为实例化 `PalaceClient`
- `mybot/gateway/telegram.py` — 同上
- `mybot/__main__.py` — 移除 `palace` CLI 子命令入口
- `mybot/tools/__init__.py` — 加载新的 PalaceTool
- `CLAUDE.md` — 更新架构描述，增加 soho-twin-towers 引用
- `config.yaml` — palace 配置改为 `base_url: http://localhost:8004`

## 7 迁移步骤

1. 创建 `soho-twin-towers/` 项目，初始化 git、pyproject.toml
2. 从 mybot 复制 `palace/` 代码和 `tests/palace/`
3. 搭建 `gateway/` HTTP 层，内置 LLM client
4. 复制 `data/palace.db`，验证所有测试通过
5. 新增 gateway 层的 API 集成测试
6. mybot 侧新建 `PalaceClient`，修改 gateway 层初始化逻辑
7. mybot 删除 `mybot/palace/` 和 `tests/palace/`
8. 端到端验证：启动 soho-twin-towers:8004 → mybot Telegram → 三道闸实测
