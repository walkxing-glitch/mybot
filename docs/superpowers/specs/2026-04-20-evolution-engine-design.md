# Evolution Engine — 自动进化系统设计

> mybot 内置的自动进化引擎，通过 Heartbeat 常驻循环驱动三条进化线：
> Skill 自学习、自我评估调优、知识自生长。

## 1. 架构总览

```
mybot 进程
  ├─ Telegram polling (已有)
  ├─ Agent.chat() (已有)
  └─ Heartbeat loop (新增)
       ├─ 每 30 分钟 tick
       ├─ tick 逻辑：
       │   ├─ SkillForge: 处理对话后积攒的 skill 候选
       │   ├─ Mirror: 距上次 ≥ 24h → 跑自评
       │   └─ Scout: 距上次 ≥ 7 天 → 跑知识探索
       └─ 用户对话期间自动让路 (defer)
```

三个子系统的产出全部进入统一的 `evolution_queue`，通过 Telegram `/evo` 命令审批。

### 依赖关系

- SkillForge → agent 对话日志 + LLM
- Mirror → evolution_queue 中的 chat_event + palace + LLM
- Scout → palace + ontology engine + web_search + LLM

不新增外部依赖，只用 asyncio.sleep 做心跳，SQLite 做存储。

## 2. Heartbeat 引擎

### 实现位置

`mybot/evolution/heartbeat.py`，新增 `HeartbeatLoop` 类。在 `Agent.__init__()` 中创建，`Agent` 启动时 `asyncio.create_task(self._heartbeat.run())`。

### 核心循环

```python
async def run(self):
    while True:
        await asyncio.sleep(self.interval)  # 默认 1800s
        if self._agent_busy:  # 检查 session lock
            continue
        await self._tick()

async def _tick(self):
    await self.skill_forge.process_pending()
    if self._should_run_mirror():
        await self.mirror.run()
    if self._should_run_scout():
        await self.scout.run()
```

### 让路机制

HeartbeatLoop 持有 Agent 的 session lock 引用。tick 前检查是否有活跃对话，有则跳过本轮。tick 执行中如果用户发消息，当前 tick 的 LLM 调用完成后让出，不中断正在进行的 LLM 请求。

### 配置

```yaml
heartbeat:
  enabled: true
  interval_seconds: 1800
  skill_forge:
    enabled: true
  mirror:
    interval_hours: 24
    enabled: true
  scout:
    interval_hours: 168
    enabled: true
```

## 3. SkillForge — Skill 自学习

### 什么算一个 skill

当 agent 在一次对话中用了 3+ 步 tool 调用链完成一个任务，且用户没有表达不满（不含"不对"、"错了"、"重来"等负面信号），这就是一个 skill 候选。

### Skill 存储格式

文件存储在 `data/skills/{name}.yaml`，不入数据库：

```yaml
name: weekly_expense_review
trigger: "用户问及某段时间的消费/花销/开支"
steps:
  - tool: palace
    operation: list_day_drawers
    params_template: { date: "{each_date_in_range}" }
    loop: date_range
  - tool: palace
    operation: get_raw_conversation
    params_template: { drawer_id: "{matched_drawer_ids}" }
  - action: summarize
    prompt_template: "汇总以下消费记录..."
confidence: 0.0
use_count: 0
status: proposed
```

### 提取流程（对话结束后）

1. **筛选**：本轮 tool_calls ≥ 3 且无负面信号
2. **去重**：用 trigger 描述和已有 skill 做相似度比较（embedding cosine），>0.85 跳过
3. **提取**：调 LLM，输入本轮完整对话，输出结构化 skill YAML
4. **入队**：写入 `evolution_queue`，status=proposed，type=skill

### 使用流程（对话开始时）

1. 加载所有 `status: active` 且 `confidence >= 0.3` 的 skill
2. 把 skill 的 trigger 列表注入 system prompt
3. LLM 自行判断是否套用某个 skill 的 steps
4. 用户满意 → `confidence += 0.1, use_count += 1`
5. 用户不满意 → `confidence -= 0.2`
6. `confidence < -0.5` → 自动标记为 `retired`

### 实现位置

`mybot/evolution/skill_forge.py`

## 4. Mirror — 自我评估与调优

### 数据采集

在 `Agent.chat()` 每轮结束时写一条 chat_event 到 evolution_queue：

```json
{
    "type": "chat_event",
    "session_id": "...",
    "timestamp": "...",
    "tool_calls": [{"name": "palace", "success": true, "latency_ms": 230}],
    "memory_hit": true,
    "negative_signal": false,
    "turn_count": 4
}
```

### 评估维度

| 维度 | 衡量方式 | 数据来源 |
|------|---------|---------|
| 工具效率 | 平均 tool 调用次数/任务，失败率 | chat_event.tool_calls |
| 用户满意度 | 负面信号比例 | chat_event.negative_signal |
| 记忆命中 | palace context 返回非空比例 | chat_event.memory_hit |

### 评估流程（每 24 小时）

1. **汇总**：从 evolution_queue 捞过去 24h 的 chat_event
2. **统计**：计算三维度指标
3. **选样**：从近期对话中选最好、最差、最典型各 1 段
4. **调 LLM**：输入统计 + 选样，输出：
   - 整体评分 (1-10)
   - 发现的问题（最多 3 条）
   - 调优建议（最多 3 条）

### 调优建议类型

```yaml
- type: prompt_tweak
  target: system_prompt
  suggestion: "当用户问消费问题时优先用 palace 查近 7 天"

- type: param_adjust
  target: palace.retriever.top_k_south
  current: 5
  suggested: 8
  reason: "记忆命中率偏低"

- type: tool_preference
  suggestion: "palace 应支持 date_range 批量查询"
```

建议写入 evolution_queue，status=proposed。

### 评估报告

生成到 `data/evolution/mirror/YYYY-MM-DD.md`，摘要推送 Telegram：

```
🪞 Mirror 日报 (04-20)
评分: 7.2/10 | 对话 12 轮 | 工具成功率 91%
建议: palace top_k 5→8（记忆命中偏低）
/evo review 查看详情
```

### 实现位置

`mybot/evolution/mirror.py`

## 5. Scout — 知识自生长

### 三条探索路径

| 路径 | 做什么 | 写入 |
|------|--------|------|
| 对话回溯 | 近 7 天对话中提取本体论不存在的实体/关系 | evolution_queue (ontology_entity) |
| 记忆补全 | 扫描 atrium active facts，检查过时/矛盾 | evolution_queue (atrium_update) |
| 外部探索 | 基于用户关注领域，web_search 搜新动态 | evolution_queue (knowledge_digest) |

### 对话回溯流程

1. 从 soho-twin-towers 拉近 7 天 south_drawer summaries
2. 调 LLM 提取实体和关系
3. 调 ontology engine 查询是否已存在
4. 差集写入 evolution_queue

```yaml
- type: ontology_entity
  content: "soho-twin-towers"
  entity_type: "project"
  relations:
    - { type: "serves", target: "mybot" }
  source: "对话回溯 2026-04-20"
```

### 记忆补全流程

1. 拉取 atrium 所有 active entries
2. 对每条 fact 调 LLM：还成立吗？有近期证据支持/反驳吗？
3. 标记为 confirm / stale / conflict
4. stale 和 conflict 进 evolution_queue 待审批

### 外部探索流程

1. 调 ontology engine 获取用户核心关注领域
2. 每个领域生成 1-2 个搜索查询
3. web_search 获取结果
4. LLM 筛选相关内容，生成知识摘要
5. 写入 evolution_queue (knowledge_digest)

### 探索报告

```
🔭 Scout 周报 (04-14 ~ 04-20)
发现: 3 个新实体候选 | 2 条 fact 疑似过时 | 1 条领域动态
/evo review 查看详情
```

### 实现位置

`mybot/evolution/scout.py`

## 6. Evolution Queue

### 数据表

存储在 `data/evolution.db`：

```sql
CREATE TABLE evolution_queue (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    source      TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'proposed',
    priority    INTEGER DEFAULT 0,
    payload     TEXT NOT NULL,
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TIMESTAMP,
    applied_at  TIMESTAMP,
    expires_at  TIMESTAMP
);

CREATE INDEX idx_evo_status ON evolution_queue(status);
CREATE INDEX idx_evo_type ON evolution_queue(type, status);
CREATE INDEX idx_evo_source ON evolution_queue(source);
```

### type 枚举

- `chat_event` — Mirror 原始数据，不走审批
- `skill` — SkillForge 产出
- `prompt_tweak` — Mirror 建议
- `param_adjust` — Mirror 建议
- `tool_preference` — Mirror 建议（仅提醒）
- `ontology_entity` — Scout 产出
- `atrium_update` — Scout 产出
- `knowledge_digest` — Scout 产出

### status 状态机

```
proposed → approved → applied
    ↓          
  rejected    
    ↓
proposed → expired (30 天未处理)
```

### 过期清理

Heartbeat 每次 tick 时检查 `expires_at < now` 的 proposed 条目，自动标记为 expired。chat_event 类保留 30 天后自动删除。

## 7. `/evo` Telegram 命令

注册为 Telegram command handler（不是 tool）：

| 命令 | 功能 |
|------|------|
| `/evo` | 待审批数量摘要 |
| `/evo review` | 逐条展示 proposed，按 priority 降序 |
| `/evo approve <id>` | 批准并自动应用 |
| `/evo reject <id>` | 驳回 |
| `/evo approve all` | 批量批准 |
| `/evo history` | 近 7 天已处理条目 |
| `/evo stats` | 整体统计（skill 数、通过率、Mirror 评分趋势） |

### 审批后自动应用

| type | 应用方式 |
|------|---------|
| skill | 写入 `data/skills/{name}.yaml`，status=active |
| prompt_tweak | 追加到 `data/evolution/prompt_patches.yaml`，启动时合并到 system prompt |
| param_adjust | 更新 `data/evolution/param_overrides.yaml`，运行时覆盖 config |
| tool_preference | 仅标记 applied，提醒手动实现 |
| ontology_entity | 调 ontology engine HTTP API 写入 |
| atrium_update | 调 soho-twin-towers HTTP API 更新/归档 |
| knowledge_digest | 写入 `data/evolution/digests/`，注入 system prompt 参考上下文 |

## 8. 渐进式自主

`data/evolution/autonomy.yaml`：

```yaml
auto_approve:
  skill: false
  prompt_tweak: false
  param_adjust: false
  ontology_entity: false
  atrium_update: false
  knowledge_digest: false
```

改为 true 后该 type 的 proposed 自动跳过审批直接应用。

## 9. 文件结构

```
mybot/
├── evolution/
│   ├── __init__.py
│   ├── heartbeat.py       # HeartbeatLoop
│   ├── skill_forge.py     # SkillForge
│   ├── mirror.py          # Mirror
│   ├── scout.py           # Scout
│   ├── queue.py           # EvolutionQueue (SQLite 操作)
│   └── applier.py         # 审批后自动应用逻辑
├── gateway/
│   └── telegram.py        # 新增 /evo 命令 handler
├── agent.py               # 修改：启动 heartbeat，chat 后写 chat_event
└── config.py              # 新增 HeartbeatConfig

data/
├── evolution.db            # evolution_queue 表
├── evolution/
│   ├── autonomy.yaml
│   ├── prompt_patches.yaml
│   ├── param_overrides.yaml
│   ├── mirror/             # 日报
│   └── digests/            # 知识卡片
└── skills/                 # skill YAML 文件
```

## 10. 灵感来源

- **OpenClaw**: Heartbeat 执行模型 + 自生成 Skills
- **Hermes Agent**: 三层记忆 + Skill 自我进化 + 每 15 任务自评
- **Manus**: 规划-执行循环 + 失败时重规划

核心差异：mybot 的进化系统通过 evolution_queue + 审批流程实现渐进式自主，不是一开始就全自动。

## 11. 补充约束

### LLM 调用预算

每次 tick 的 LLM 调用设上限，避免失控：

| 子系统 | 每次运行最大 LLM 调用 | 预估 token |
|--------|----------------------|-----------|
| SkillForge | 1 次/每个候选对话 | ~2K input + 500 output |
| Mirror | 1 次汇总 + 1 次评估 | ~4K input + 1K output |
| Scout | 3 次（回溯 + 补全 + 探索） | ~8K input + 2K output |

config.yaml 中可配置 `heartbeat.max_llm_calls_per_tick: 10`，超过则推迟到下一个 tick。

### chat_event 数据采集

`Agent._run_tool_loop()` 需要改造：在循环中记录每次 tool 调用的 name、success、latency_ms，作为 `tool_log: list[dict]` 返回。`Agent.chat()` 拿到 tool_log 后组装 chat_event 写入 evolution_queue。

### Skill 去重的 embedding

通过 HTTP 调用 soho-twin-towers 的 doubao embedder（复用已有基础设施），不在 mybot 内引入 embedder 依赖。需要在 soho-twin-towers 新增一个 `/embed` 端点，接受文本返回 embedding 向量。
