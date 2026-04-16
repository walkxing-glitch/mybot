# MyBot Agent — 设计规格

> 个人 AI Agent，以本体论系统为大脑，具备高级记忆引擎，支持多模型、多工具、多入口。

---

## 1. 项目定位

MyBot 是一个 Hermes-inspired 的个人 AI Agent 框架，核心差异化在于：

- **本体论大脑**：以 myontology 的实体知识图谱（人/商户/交易/关系）作为 agent 的知识底座
- **高级记忆引擎**：用户画像演化、遗忘衰减、跨会话时间推理
- **Neural-Twin 集成**：调用数字分身的决策预测和行为分析能力
- **多模型支持**：Claude / OpenAI / 豆包 / Ollama 一行切换

使用场景：
1. 个人助手——通过 Telegram/CLI 对话，执行搜索、文件管理、提醒等任务
2. 开发工具——代码生成、项目文件读写、Shell 命令执行
3. 数字分身交互层——查询 Neural-Twin 的预测、查询本体论的知识

---

## 2. 技术栈

| 层 | 选型 | 理由 |
|----|------|------|
| 语言 | Python 3.11+ | 与 Neural-Twin / myontology 同栈 |
| LLM 接口 | LiteLLM | 统一多模型 function-calling，零适配成本 |
| Telegram | python-telegram-bot | 成熟稳定，长轮询模式 |
| 记忆存储 | SQLite (via aiosqlite) | 轻量，单文件，容器友好 |
| HTTP 客户端 | httpx | 异步调用 myontology API |
| CLI 渲染 | rich | 彩色输出，工具调用可视化 |
| 容器 | Docker + docker-compose | 独立容器，通过 external network 连接 myontology |
| 包管理 | uv | 快速，现代 Python 包管理 |

---

## 3. 架构

### 3.1 部署拓扑

```
┌─── docker-compose (myontology) ───────────────┐
│  postgres:5432    api:8003    engine:...       │
└───────────────────┬───────────────────────────┘
                    │ docker network (myontology_default)
┌───────────────────┴───────────────────────────┐
│              mybot 容器                        │
│                                               │
│  ┌─ Gateway ─────────────────────────────┐    │
│  │  CLI (stdin/stdout)                   │    │
│  │  Telegram (long-polling)              │    │
│  └───────────────┬───────────────────────┘    │
│                  │                             │
│  ┌─ Agent Core ──┴───────────────────────┐    │
│  │  LiteLLM (multi-model)               │    │
│  │  Tool Router                          │    │
│  │  Conversation Manager                 │    │
│  └───┬───────────────┬──────────────────┘    │
│      │               │                        │
│  ┌─ Tools ──┐  ┌─ Memory Engine ────────┐    │
│  │ shell    │  │ Profile Evolution      │    │
│  │ code     │  │ Decay System           │    │
│  │ web      │  │ Temporal Reasoning     │    │
│  │ ontology │  │ SQLite Store           │    │
│  │ neural   │  └────────────────────────┘    │
│  │ calendar │                                 │
│  │ memory   │  Volume: ./data → /app/data    │
│  └──────────┘                                 │
└───────────────────────────────────────────────┘
```

### 3.2 Agent 主循环

```
用户输入
  → 记忆引擎：检索相关记忆 + 用户画像摘要
  → 组装 messages：system_prompt + 画像 + 相关记忆 + 对话历史 + 用户消息
  → LLM 调用（带 tools 定义）
  → 如果 LLM 返回 tool_calls：
      → 并行执行各工具
      → 将结果追加到 messages
      → 再次调用 LLM（循环，最多 10 轮）
  → 如果 LLM 返回文本：
      → 输出给用户
  → 异步触发：记忆整理（摘要提取、画像更新、衰减计算）
```

### 3.3 对话管理

- 每个 session_id 维护独立的 messages 列表
- 对话超过 token 阈值时，旧消息由 LLM 压缩为摘要
- session 元数据存入 SQLite，支持跨重启恢复

---

## 4. 记忆引擎

### 4.1 数据模型

```sql
-- 情景记忆
CREATE TABLE memories (
    id TEXT PRIMARY KEY,
    content TEXT NOT NULL,
    memory_type TEXT NOT NULL,        -- episode / fact / preference / observation
    created_at TIMESTAMP NOT NULL,
    last_accessed TIMESTAMP NOT NULL,
    access_count INTEGER DEFAULT 0,
    salience REAL NOT NULL,           -- 当前活跃度，决定是否参与检索
    base_importance REAL NOT NULL,    -- LLM 评估的初始重要度 (0.0-1.0)
    tags TEXT DEFAULT '[]',           -- JSON 标签数组
    source_session TEXT,              -- 来源会话 ID
    temporal_context TEXT,            -- 时间语义标签："周三下午"/"月初"/"冬天"
    status TEXT DEFAULT 'active'      -- active / dormant / archived
);

-- 用户画像
CREATE TABLE profile_traits (
    id TEXT PRIMARY KEY,
    dimension TEXT NOT NULL,          -- behavior / interest / decision_style / social / focus
    key TEXT NOT NULL,
    value TEXT NOT NULL,
    confidence REAL DEFAULT 0.5,      -- 置信度 (0.0-1.0)
    evidence_count INTEGER DEFAULT 1, -- 支撑证据数
    first_observed TIMESTAMP,
    last_updated TIMESTAMP,
    trend TEXT DEFAULT 'stable'       -- rising / stable / declining
);

-- 会话摘要
CREATE TABLE session_summaries (
    id TEXT PRIMARY KEY,
    session_id TEXT NOT NULL,
    summary TEXT NOT NULL,
    topics TEXT DEFAULT '[]',         -- JSON 主题标签
    created_at TIMESTAMP NOT NULL,
    memory_ids TEXT DEFAULT '[]'      -- 关联的 memory IDs
);
```

### 4.2 用户画像演化

每次对话结束后异步执行：

1. LLM 收到当前画像 + 本次对话摘要
2. 输出结构化 diff（JSON）：
   - `update`：修改已有 trait 的 value
   - `strengthen`：提升 trait 的 confidence（+0.1~0.3）
   - `weaken`：降低 trait 的 confidence（-0.1~0.3）
   - `new_insight`：新发现的 trait
   - `trend_change`：标记趋势变化（stable → rising/declining）
3. 合并进 `profile_traits` 表

画像维度：
- **behavior**：消费习惯、作息、通勤方式
- **interest**：技术栈、投资领域、生活爱好，带强度权重
- **decision_style**：果断/谨慎、效率优先、偏好对比
- **social**：关键人物、互动频率、关系性质
- **focus**：近期关注点，衰减最快的维度

### 4.3 遗忘衰减机制

Salience 计算公式：

```
salience = base_importance × recency_decay × access_boost × relevance_multiplier

recency_decay = e^(-λt)          -- t = 距创建时间的天数，λ = ln(2)/half_life
access_boost = 1 + 0.2 × ln(1 + access_count)
relevance_multiplier = 和当前画像 focus 维度的关联度 (0.5-2.0)
```

默认参数：
- `half_life = 30 天`（普通记忆）
- `half_life = 180 天`（fact 类型，衰减更慢）
- `half_life = 365 天`（preference 类型，几乎不衰减）

定期整理（每 10 次对话触发）：
- `salience < 0.1`：标记为 dormant（不参与主动检索，但可被精确查询唤醒）
- `salience < 0.01`：标记为 archived
- 相似记忆合并：LLM 判断两条记忆是否描述同一事实，合并为一条并累加 evidence

### 4.4 跨会话时间推理

检索记忆时，注入时间上下文到 LLM prompt：

```
系统检索到以下相关记忆（按相关度排序）：

1. [2026-03-15, 31天前] 用户说"最近在研究 agent 框架"
   salience: 0.45, 访问次数: 3
   
2. [2026-04-01, 15天前] 用户决定"本体论不要接 neural-twin"
   salience: 0.72, 访问次数: 1

3. [2026-04-10, 6天前] 用户开发 Neural-Twin 判别器
   salience: 0.89, 访问次数: 2

注意：如果用户当前意图与历史决策矛盾，主动指出变化。
注意：标记为 declining trend 的画像 trait 可能已不准确。
```

时间语义标签自动生成：LLM 在存入记忆时标注 temporal_context（"工作日晚上"/"周末"/"月初发薪后"），支持按时间模式检索。

---

## 5. 工具系统

### 5.1 工具接口

```python
from dataclasses import dataclass
from typing import Any

@dataclass
class ToolResult:
    success: bool
    output: str
    error: str | None = None

class BaseTool:
    name: str
    description: str
    parameters: dict          # JSON Schema

    async def execute(self, **params) -> ToolResult
```

工具注册：每个工具文件导出一个 Tool 实例，`tools/__init__.py` 自动扫描注册。Agent 启动时根据 `config.yaml` 的 `enabled_tools` 列表决定加载哪些。

### 5.2 工具清单

| 工具名 | 文件 | 功能 | 调用方式 |
|--------|------|------|---------|
| `shell` | `tools/shell.py` | 执行 Shell 命令 | 本地 subprocess，白名单过滤 |
| `code` | `tools/code.py` | 读/写/搜索项目文件 | 本地文件系统，限定工作目录 |
| `web_search` | `tools/web.py` | 搜索互联网 | DuckDuckGo API（免 key） |
| `web_fetch` | `tools/web.py` | 抓取网页转 markdown | httpx + html2text |
| `ontology` | `tools/ontology.py` | 查实体/关系/交易/消费模式 | HTTP → myontology API :8003 |
| `neural_twin` | `tools/neural_twin.py` | 决策预测/习惯分析/异常检测 | HTTP 或 import neural-twin |
| `calendar` | `tools/calendar.py` | 创建/查询/删除提醒和日程 | SQLite 本地存储 |
| `memory` | `tools/memory_tool.py` | 主动存储/搜索/管理记忆 | 调记忆引擎内部接口 |

### 5.3 安全边界

- **shell**：可配置命令白名单（默认：ls, cat, grep, find, python, git, pip, uv, docker）；禁止 rm -rf, sudo, chmod 777 等危险操作；执行超时 30s
- **code**：限定工作目录（可在 config.yaml 配置），禁止写入系统路径
- **所有工具**：执行超时默认 30s，可按工具单独配置

---

## 6. Gateway

### 6.1 统一接口

```python
class Agent:
    async def chat(self, session_id: str, message: str) -> AsyncIterator[str]:
        """核心对话方法，所有 gateway 调用这一个入口。"""
```

Gateway 只负责 I/O 格式转换和平台适配。

### 6.2 CLI

- REPL 循环，rich 渲染
- 工具调用过程实时显示（工具名 + 参数 + 结果摘要）
- 支持 Ctrl+C 中断当前请求
- 支持 `/model <name>` 切换模型、`/memory` 查看记忆统计等内置命令

### 6.3 Telegram

- python-telegram-bot，长轮询模式（无需公网 IP）
- 对话历史按 chat_id 隔离
- 长任务先回复"处理中..."，完成后发新消息
- 支持接收图片（存到本地，未来可接视觉模型分析）
- Bot token 通过 `.env` 配置

---

## 7. 配置

```yaml
# config.yaml
model:
  default: "claude-sonnet-4-20250514"
  fallback: "deepseek-chat"
  
api_keys:
  # 实际 key 在 .env 里，这里只定义变量名映射
  anthropic: "${ANTHROPIC_API_KEY}"
  openai: "${OPENAI_API_KEY}"
  doubao: "${DOUBAO_API_KEY}"

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
  shell:
    allowed_commands: [ls, cat, grep, find, python, git, pip, uv, docker]
    timeout: 30
  code:
    workspace_dirs:
      - /Users/ddn/Developer
  ontology:
    api_url: "http://myontology-api:8003"  # Docker 网络内部地址

memory:
  decay:
    default_half_life_days: 30
    fact_half_life_days: 180
    preference_half_life_days: 365
  consolidation_interval: 10  # 每 N 次对话触发整理
  
gateway:
  telegram:
    token: "${TELEGRAM_BOT_TOKEN}"
    polling_mode: true
```

---

## 8. 目录结构

```
mybot/
├── docker-compose.yml            # mybot 容器定义
├── Dockerfile
├── config.yaml                   # 主配置
├── .env.example                  # API keys 模板
├── pyproject.toml
├── mybot/
│   ├── __init__.py
│   ├── __main__.py               # python -m mybot 入口
│   ├── agent.py                  # Agent 主循环
│   ├── llm.py                    # LiteLLM 多模型封装
│   ├── config.py                 # 配置加载（YAML + .env）
│   ├── tools/
│   │   ├── __init__.py           # 工具自动注册
│   │   ├── base.py               # BaseTool / ToolResult
│   │   ├── shell.py
│   │   ├── code.py
│   │   ├── web.py
│   │   ├── ontology.py
│   │   ├── neural_twin.py
│   │   ├── calendar.py
│   │   └── memory_tool.py
│   ├── memory/
│   │   ├── __init__.py
│   │   ├── engine.py             # 记忆引擎核心（检索、存入、整理）
│   │   ├── profile.py            # 用户画像演化
│   │   ├── decay.py              # 遗忘衰减计算
│   │   ├── temporal.py           # 时间推理
│   │   └── store.py              # SQLite 持久化层
│   ├── gateway/
│   │   ├── __init__.py
│   │   ├── cli.py
│   │   └── telegram.py
│   └── utils.py
├── data/                         # SQLite DB（volume 挂载）
├── tests/
│   ├── test_agent.py
│   ├── test_memory.py
│   └── test_tools.py
└── docs/
    └── superpowers/
        └── specs/
            └── 2026-04-16-mybot-agent-design.md
```

---

## 9. 第一天目标

今天结束时应该能做到：

1. `docker-compose up` 启动 mybot 容器
2. CLI 模式下和 agent 对话
3. agent 能调用 shell / code / web_search / ontology / memory 工具
4. 记忆引擎基础功能可用（存入、检索、基础衰减）
5. 用户画像能在对话后自动更新
6. Telegram bot 能收发消息（如果 token 配好了）
