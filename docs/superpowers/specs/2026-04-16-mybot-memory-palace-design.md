# MyBot 记忆系统重构：丽泽园（Lize Memorial Palace）

**状态**：Design approved, pending spec review
**日期**：2026-04-16
**作者**：邢智强 + Claude
**前置事件**：2026-04-16「记忆铁锈」bug（memories 表把工具失败叙述抽取为事实，污染后续对话）
**参考**：MemPalace（https://github.com/MemPalace/mempalace，verbatim + 语义检索路线）、丽泽 SOHO 双塔建筑（南北塔 + 中庭空间隐喻）

---

## 1. 动机

### 1.1 要解决的问题

2026-04-16 发生严重事件："铁锈 bug"。

- 上午 09:21 / 09:28 时段，mybot 的工具路径配置错误（`/api/v1/*` 指向不存在的端点），用户问"我在北京花了多少钱"几次都失败
- **memory engine 的 `_extract_memorables()` 将失败叙述错误地抽取为长期事实**，写入 `memories` 表：
  - "本体论知识图谱服务（端口8003）当前不可用，影响了地理消费数据的查询"
  - "用户的记忆系统中没有存储与'北京 消费 花费 金额'相关的信息"（自指）
  - 等 11 条污染记忆
- 下午修复代码、服务、launchd 常驻后，Telegram 里同问题依然回复"本体论服务不可用"——**LLM 在 prompt 里看到了语义召回注入的那些错误记忆，照实复读**
- 手工清洗 `memory.db`（见 `data/memory.db.bak-20260416-172942`）后才恢复

### 1.2 现有记忆系统的根本缺陷

| 问题 | 位置 | 危害 |
|---|---|---|
| LLM 从对话中**抽取"事实"** | `engine.py:367 _extract_memorables()` | 失败叙述被当事实存 |
| 召回完全依赖 FTS5 关键词 | `engine.py:143-176 recall()` | 语义弱、同义词搜不出 |
| 画像自动演化无证据链 | `profile.py apply_diff()` | 改了不知道是谁、为什么改的 |
| 没有写入黑名单 | 全系统无 | "不可用/失败/超时"类叙述能直接入库 |
| 污染后无自动修复 | 全系统无 | 需人工 SQL 扫库 |

### 1.3 设计目标

- **彻底杜绝"失败叙述当事实"**的污染路径
- **语义检索**能力（不只是关键词）
- **跨年永久保存** 不丢失历史
- **用户可审批**重要记忆的入驻
- **离线优先** 不依赖云 API
- **对 Agent 代码最小改动**（内部重写，接口几乎不变）

---

## 2. 架构总览

### 2.1 三层子系统

```
               ┌────────────────────────────────────┐
               │         中庭 (Atrium)               │
               │  跨年永久层 · 用户规则/偏好/事实      │
               │  写入三道闸：黑名单 + 显式 + 证据链    │
               └─────────────────┬──────────────────┘
                                 │ 主动注入每次对话
              ┌──────────────────┼──────────────────┐
              ↓                                     ↓
    ┌────────────────────┐              ┌────────────────────┐
    │    北塔 (2026)      │ ←──坐标配对→│    南塔 (2026)      │
    │   原始对话          │  初始 1:1    │   摘要 + 索引        │
    │  楼 1..365 × 20 × 20│  溢出后 N:1  │  楼 1..365 × 20 × 20│
    └────────────────────┘              └────────────────────┘
           不检索、只保存                 主检索入口

    ┌────────────────────┐     ┌────────────────────┐
    │    北塔 (2027)      │ ← → │   南塔 (2027)       │
    └────────────────────┘     └────────────────────┘
    ...依年份堆叠，永不覆盖
```

### 2.2 三子系统职责

| 子系统 | 职责 | 写入触发 | 被检索？ | 生命周期 |
|---|---|---|---|---|
| **北塔** | 保存**原封不动**的对话原文 | 每轮 session 结束 | ❌ 否（只通过坐标取） | 永久不可改 |
| **南塔** | **摘要 + 向量 + 坐标**，供语义检索 | 每轮 session 结束（与北塔同步） | ✅ 主通道 | 永久不可改 |
| **中庭** | **跨天规则/偏好/事实**，主动注入 prompt | 用户显式 OR inferred 证据链提案通过 | ✅ 精确查 + 主动注入 | 永久、**可审改**、可降级 |

### 2.3 空间坐标

- **年份塔**（Year Tower）：每年一座南北塔对，2026 / 2027 / ... 堆叠（园区）
- **楼层**（Floor）：1 to 365，对应 day-of-year。闰年第 366 天并入第 365 楼
- **房间**（Room）：每层 20 间
  - Room 1-10: **固定主题**（配置：消费/工作/人际/健康/学习/技术/项目/家庭/出行/情绪）
  - Room 11-19: **动态主题**（LLM 按天命名）
  - Room 20: **杂项兜底**
- **抽屉**（Drawer）：每房 20 格

**单塔容量**：365 × 20 × 20 = 146,000 格；实际日均用 ~50 格，真实占用极低。

### 2.4 设计哲学三条

1. **数据流单向**：原始对话 → 北塔（落地）→ 南塔（浓缩可搜）→ 中庭（升华为规则，有门槛）。**中庭错了不会污染南北塔**。
2. **读写通道解耦**：写路径按天落格；读路径通过南塔向量检索 + 中庭精确查询并行。
3. **防污染纵深防御**：
   - 北塔无 LLM 抽取 → 不可能污染
   - 南塔 LLM 只做摘要不做 fact 抽取 → 摘要不被当规则用
   - 中庭才是规则层 → 三道闸把控（黑名单 + 显式 + 证据链）+ 30 天巡检降级

### 2.5 坐标对应关系

- **初始写入**：每个新 chunk 同时写入 `N-y-f-r-d` 与 `S-y-f-r-d`（坐标相同）
- **抽屉溢出合并**（§4.3）：南塔目标抽屉的 `north_ref_ids` 变为**列表**（N:1），不再 1:1
- **坐标永久稳定**：合并只追加 north 引用，不删除旧 north 抽屉；查询可反查南塔任一抽屉背后的所有原始对话

---

## 3. 数据模型

### 3.1 存储位置

- 新库 `data/palace.db`（跟旧 `memory.db` 分文件，清零重启不互扰）
- 旧 `memory.db` 保留为 `memory.db.legacy-20260416.bak`，新系统不读

### 3.2 SQLite Schema

#### 北塔：原始对话

```sql
CREATE TABLE north_drawer (
    id            TEXT PRIMARY KEY,       -- "N-2026-107-5-7"
    year          INTEGER NOT NULL,
    floor         INTEGER NOT NULL,        -- 1..365
    room          INTEGER NOT NULL,        -- 1..20
    drawer        INTEGER NOT NULL,        -- 1..20
    date          TEXT NOT NULL,           -- '2026-04-16'
    raw_messages  TEXT NOT NULL,           -- JSON array
    message_count INTEGER,
    created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (year, floor, room, drawer)
);
CREATE INDEX idx_north_date ON north_drawer(date);
```

#### 南塔：摘要 + 向量 + BM25

```sql
CREATE TABLE south_drawer (
    id             TEXT PRIMARY KEY,       -- "S-2026-107-5-7"
    north_ref_ids  TEXT NOT NULL,          -- JSON list（合并后多个）
    year           INTEGER NOT NULL,
    floor          INTEGER NOT NULL,
    room           INTEGER NOT NULL,
    drawer         INTEGER NOT NULL,
    date           TEXT NOT NULL,
    room_type      TEXT NOT NULL,          -- 'fixed' | 'dynamic' | 'misc'
    room_label     TEXT NOT NULL,          -- "消费"/"技术讨论"/"杂项"
    drawer_topic   TEXT NOT NULL,          -- "北京消费问答"
    summary        TEXT NOT NULL,          -- ≤200 字
    keywords       TEXT,                   -- JSON array
    merge_count    INTEGER DEFAULT 1,
    created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (year, floor, room, drawer)
);
CREATE INDEX idx_south_date      ON south_drawer(date);
CREATE INDEX idx_south_room_type ON south_drawer(room_type, room_label);

-- 向量（sqlite-vec 扩展）
CREATE VIRTUAL TABLE south_vec USING vec0(
    drawer_id TEXT PRIMARY KEY,
    embedding FLOAT[1024]
);

-- BM25
CREATE VIRTUAL TABLE south_fts USING fts5(
    drawer_id UNINDEXED,
    summary,
    keywords,
    tokenize='unicode61'
);
```

#### 中庭：永久规则/偏好/事实

```sql
CREATE TABLE atrium_entry (
    id                  TEXT PRIMARY KEY,
    entry_type          TEXT NOT NULL,    -- 'rule' | 'preference' | 'fact'
    content             TEXT NOT NULL,
    source_type         TEXT NOT NULL,    -- 'explicit' | 'inferred'
    status              TEXT NOT NULL,    -- 'pending' | 'active' | 'archived' | 'rejected'
    evidence_drawer_ids TEXT,             -- JSON list → south_drawer.id
    evidence_count      INTEGER DEFAULT 0,
    confidence          REAL DEFAULT 1.0,
    has_conflict_with   TEXT,             -- 冲突时指向另一 entry.id
    proposed_at         TIMESTAMP,
    approved_at         TIMESTAMP,
    rejected_at         TIMESTAMP,
    last_confirmed_at   TIMESTAMP,
    last_reviewed_at    TIMESTAMP,
    created_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at          TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX idx_atrium_status ON atrium_entry(status);
CREATE INDEX idx_atrium_type   ON atrium_entry(entry_type, status);

-- 中庭 fact 类的向量索引（rule/preference 不需要，总注入）
CREATE VIRTUAL TABLE atrium_vec USING vec0(
    entry_id  TEXT PRIMARY KEY,
    embedding FLOAT[1024]
);

-- 审计历史
CREATE TABLE atrium_changelog (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    entry_id   TEXT NOT NULL,
    old_value  TEXT,              -- JSON snapshot
    new_value  TEXT,
    action     TEXT,               -- 'create'|'approve'|'reject'|'edit'|'archive'|'resurrect'|'downgrade'|'merge'
    actor      TEXT,               -- 'user_cli'|'auto_approve'|'nightly_inspect'|'conflict_merge'
    changed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 黑名单硬保险（触发器）
CREATE TRIGGER atrium_blacklist_guard
BEFORE INSERT ON atrium_entry
WHEN NEW.content LIKE '%不可用%'
  OR NEW.content LIKE '%未能找到%'
  OR NEW.content LIKE '%服务中断%'
  OR NEW.content LIKE '%超时%'
  OR NEW.content LIKE '%工具报错%'
  OR NEW.content LIKE '%无法访问%'
  OR NEW.content LIKE '%操作失败%'
  OR NEW.content LIKE '%连接失败%'
BEGIN
    SELECT RAISE(ABORT, 'Atrium blacklist pattern matched: rejecting entry');
END;
```

#### 辅助：合并日志 + 每日房间分配

```sql
CREATE TABLE drawer_merge_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    target_id   TEXT NOT NULL,
    merged_from TEXT NOT NULL,     -- JSON 旧摘要 + 原 north ref
    reason      TEXT,              -- 'drawer_overflow'|'room_overflow'
    created_at  TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE day_room_map (
    date         TEXT NOT NULL,
    room         INTEGER NOT NULL,   -- 1..20
    room_type    TEXT NOT NULL,      -- 'fixed'|'dynamic'|'misc'
    room_label   TEXT NOT NULL,
    drawer_count INTEGER DEFAULT 0,
    PRIMARY KEY (date, room)
);
```

### 3.3 配置（`config/memory_palace.yaml` 或合入 `config.yaml`）

```yaml
palace:
  db_path: data/palace.db
  current_year_scope: 3           # 默认检索最近 3 年
  embedder: bge-m3                # 本地 1024 维
  embedder_dim: 1024
  reranker: bge-reranker-v2-m3    # 本地
  top_k_south: 5                  # 最终注入南塔条数
  top_k_fact: 3                   # 中庭 fact 类注入 top k

rooms:
  fixed:
    1: 消费
    2: 工作
    3: 人际
    4: 健康
    5: 学习
    6: 技术
    7: 项目
    8: 家庭
    9: 出行
    10: 情绪
  misc_room: 20

atrium_guards:
  blacklist_patterns:              # 同触发器，代码层再过一遍
    - "不可用"
    - "未能找到"
    - "服务中断"
    - "超时"
    - "工具报错"
    - "无法访问"
    - "操作失败"
    - "连接失败"
  evidence_threshold: 3            # inferred 最少证据数
  evidence_days_span: 2            # 证据跨天数
  require_manual_approve: true     # pending 必须 CLI 批，否则永不转 active
  review_cycle_days: 30
  stale_archive_days: 90           # 规则/偏好 >90 天未确认自动归档（fact 不归档）

telegram_notify:
  atrium_audit_digest: weekly      # 巡检降级通知频率
```

### 3.4 坐标 ID 规范

- 北塔：`N-{year}-{floor:03d}-{room:02d}-{drawer:02d}`，如 `N-2026-107-05-07`
- 南塔：`S-{year}-{floor:03d}-{room:02d}-{drawer:02d}`，如 `S-2026-107-05-07`
- 中庭：UUID v4（跟坐标无关，因为不绑在天）

---

## 4. 写入流程

### 4.1 触发点

Session 结束时批量写入。Session 结束判定：
- 用户 `/end` 显式结束
- 10 分钟无新消息自动结束
- 累计 ≥30 条消息强制切 session

### 4.2 数据流（一条 session → 南北塔 + 中庭）

```
1. LLM 调用 ①：切分 + 摘要 + 关键词
   in:  messages + 当天已存在的 day_room_map
   out: chunks = [{msg_indices, drawer_topic, summary, keywords, proposed_room_label}]

2. 房间路由（确定性，不走 LLM）
   for each chunk:
     if chunk.proposed_room_label ∈ fixed 10 类别:
        → room = 对应固定号
     elif 动态房(11-19)有相同 label:
        → 合并进已有动态房
     elif 动态房未满:
        → 开新动态房
     else:
        → 房 20 杂项

3. 抽屉分配 + 溢出合并
   for each chunk in room:
     if 房间当前抽屉数 < 20:
        → 新抽屉（drawer = count + 1）
     else:
        → 溢出处理（见 §4.3）

4. 北塔写入（原封不动）
   INSERT INTO north_drawer (raw_messages=JSON of msg_indices…)

5. 南塔写入（摘要 + 向量 + BM25）
   INSERT INTO south_drawer
   + bge-m3.encode(summary) → south_vec
   + south_fts.insert

6. LLM 调用 ②（仅当 session 含用户显式声明时）
   扫描 user messages 是否含：
     "记住" / "以后别" / "我偏好" / "我的 X 是"
   → 抽取为 atrium 候选（type=explicit, status=active, confidence=0.95）
   → 黑名单预过滤 + 触发器兜底

7. 更新 day_room_map.drawer_count
```

### 4.3 溢出合并规则

**抽屉溢出**（房间已满 20 抽屉）：
- 算新 chunk 的 embedding 与该房间 20 个抽屉的 cosine 相似度
- 找 max-sim 的目标抽屉
- LLM 调用（③）：合并两段摘要成一条（"目标.summary + 新.summary → 合并摘要"）
- `target.north_ref_ids` 追加新 chunk 的 north id（不覆盖，列表式）
- `target.summary` 更新为合并结果
- `target.merge_count += 1`
- 记 `drawer_merge_log`（审计）

**房间溢出**（当天房间数 >20）：
- 固定房(1-10)永不满（每天最多 10 间预留）
- 动态房(11-19)优先合并：新 chunk 若与动态房某间语义最近 → 进该间（再按抽屉规则）
- 所有动态房都不合适 → 进房 20 杂项
- 杂项房内部按抽屉规则

### 4.4 LLM 调用总量

| 场景 | 调用次数 |
|---|---|
| 正常 session（无溢出、无显式声明） | 1 次（切分摘要） |
| Session 含显式声明 | 2 次 |
| 某房间溢出需合并 | 每次溢出 +1 次 |
| **典型 session** | **1-2 次**（vs 当前 4 次） |

### 4.5 提案流程（inferred 类）

独立 nightly job（2:00 AM），不在 session 结束时做：

```
1. 读最近 30 天所有南塔抽屉
2. LLM 调用 ④（每天 1 次）：
   "找重复 ≥3 次、跨 ≥2 天的用户偏好/规则候选"
3. 黑名单预过滤 + 触发器兜底
4. 去重：跟现有 active 条目对比（向量相似度 + LLM judge EQUIVALENT）
5. 入 atrium_entry:
   source_type='inferred', status='pending'
   evidence_drawer_ids=[...south_drawer.id...]
6. 冲突检测（见 §6.4）
7. Telegram 不推送（用户选择 CLI 批）
```

### 4.6 并发与失败

- 按 `date` 加 asyncio.Lock 串行写入
- 北塔 + 南塔同事务
- 向量/FTS 插入事务外 flush，失败可事后重建
- LLM 调用失败 → session 消息暂存 `data/pending_sessions/<session_id>.json`，下次 mybot 启动时重跑

### 4.7 Agent 接口

```python
# agent.py 的改动只有两处：
self.palace = MemoryPalace(db_path="data/palace.db", config=cfg.palace)

context = await self.palace.assemble_context(user_msg)   # 替代 memory_engine.get_context_for_prompt

asyncio.create_task(self.palace.archive_session(session_id, messages))  # 替代 memory_engine.end_session
```

---

## 5. 检索流程

### 5.1 触发点

每次用户给 mybot 发消息，第一次 LLM completion 之前：

1. **中庭**：
   - rule + preference 全部注入（总是）
   - fact 类 top 3（按 query 相关性）
2. **南塔**：top 5 抽屉摘要（混合检索）
3. **北塔**：默认不查；LLM 需要原话时通过 `palace_tool.get_raw_conversation(drawer_id)` 按需取

### 5.2 南塔检索管线

```
Query: "我在北京花了多少钱"
  │
  ├── 通道 A: 向量召回（bge-m3.encode → sqlite-vec KNN） → top 30
  │     作用: 语义近义
  │
  ├── 通道 B: BM25（FTS5 + jieba 分词） → top 30
  │     作用: 精确字面（数字/UUID/人名）
  │
  ├── RRF 合并: score = 1/(rank_A + 60) + 1/(rank_B + 60)
  │     去重 → top 60 候选
  │
  ├── Rerank: bge-reranker-v2-m3（CPU ~150ms for 60 pairs）
  │
  └── 取 top K=5
```

### 5.3 作用域

- 默认检索最近 `current_year_scope=3` 年（向量查询+metadata filter）
- 显式扩展：用户 query 含"好多年前 / 最早"等词 → 全量
- 主题列扫描：query 命中固定房类别（如"今年消费总览"→ 房 1） → 只扫该列

### 5.4 Prompt 注入格式

```markdown
## 🏛️ 你记得的用户规则与偏好（中庭·永久）

- [规则] 别启动 myontology/backend（2026-04-16 起）
- [偏好] 喜欢简洁直接的回答
- [事实] 真名邢智强（不是 ddn）
…

## 📚 可能相关的过去对话（南塔·top 5）

1. [2026-04-16 消费/北京消费问答]
   （摘要正文）
   （坐标 S-2026-107-01-01，可 get_raw_conversation 取原文）
2. ...

## 🎯 用户当前问题
{user_message}
```

### 5.5 新增工具 `palace`（BaseTool）

```yaml
name: palace
description: 查丽泽园记忆系统。按抽屉坐标取原文、列某天的抽屉、看中庭条目
parameters:
  operation: enum [get_raw_conversation, list_day_drawers, list_atrium, show_atrium_entry]
  drawer_id: string     # for get_raw_conversation
  date: string          # for list_day_drawers, 'YYYY-MM-DD'
  entry_id: string      # for show_atrium_entry
returns: JSON
```

### 5.6 Fallback 降级链

| 故障 | 降级 | 用户感知 |
|---|---|---|
| bge-m3 模型加载失败 | 只 BM25 | 语义模糊 query 召回下降 |
| sqlite-vec 扩展缺失 | 只 BM25 | 同上 |
| bge-reranker 失败 | 跳过重排，取融合 top K | 精度略降 |
| 全部向量挂 | 中庭 + BM25 top 5 | 规则仍生效 |
| palace.db 损坏 | 只注入内存缓存的中庭 | 失忆但不挂 |

### 5.7 性能预算（i7-7820HQ / 16GB, 3 年 ~150K 抽屉）

| 步骤 | 耗时 |
|---|---|
| bge-m3 encode(query) | 80ms |
| sqlite-vec KNN | 10ms |
| FTS5 BM25 | 5ms |
| RRF | <1ms |
| Rerank 60 对 | 150ms |
| **总延迟** | **~250ms** |

---

## 6. 中庭管理

### 6.1 CLI 子命令

```bash
python -m mybot memory review         # 交互式审待定候选
python -m mybot memory list           # 列条目（--type / --status 过滤）
python -m mybot memory show <id>      # 详情 + 证据
python -m mybot memory edit <id>      # 编辑
python -m mybot memory archive <id>   # 软归档
python -m mybot memory resurrect <id> # 复活
python -m mybot memory audit          # 看 needs_review / 自动归档
python -m mybot memory stats          # 总览
python -m mybot memory backup now     # 手动备份
python -m mybot memory init           # 首次初始化 palace.db
```

### 6.2 审批交互

`memory review` 交互范例：

```
$ python -m mybot memory review

[1/7] pending inferred rule
content: 用户偏好晚上 10 点后不被 Telegram 打扰
evidence (3 drawers, 2 days):
  S-2026-103-10-02 (2026-04-12): "今晚早点睡，别再发了"
  S-2026-104-10-05 (2026-04-13): "静音就好，早上看"
  S-2026-105-10-03 (2026-04-14): "别在 22 点后推送"
conflict: none
[a]pprove / [r]eject / [e]dit / [s]kip: a
→ status: pending → active, confidence=0.9
```

动作语义：
- `approve` → status: pending → active, confidence=0.9, approved_at
- `reject` → status: pending → rejected, rejected_at; 提案器记忆"同类不再提"
- `edit` → pending → active，记录 changelog
- `skip` → 下次 review 再出
- 冲突时 `keep` 旧：新 entry rejected，旧保持 active

**严格策略**：`require_manual_approve=true` → pending 不会自动转 active，必须 CLI 批。

### 6.3 30 天巡检（nightly 2 AM）

```python
for entry in atrium where status='active' and last_reviewed_at > 30 days ago:
    # 1. 以 entry.content 查南塔最近 60 天 top 5
    drawers = south_search(entry.content, scope='last_60_days', limit=5)

    # 2. LLM 对每条裁判：CONFIRM | CONTRADICT | UNRELATED
    for drawer in drawers:
        verdict = llm_judge(entry, drawer)

    # 3. 更新 confidence
    if 出现 CONTRADICT:
        confidence -= 0.2 * contradict_count
        标 needs_review（供 audit 查看）
    elif CONFIRM_count > 0:
        last_confirmed_at = now()
        confidence = min(1.0, confidence + 0.05)

    # 4. 自动退休
    if confidence < 0.3:
        status = 'archived'
    elif entry_type in ('rule','preference') and last_confirmed_at > 90 天:
        status = 'archived'
    # fact 类不自动归档（事实不过期）

    last_reviewed_at = now()
```

### 6.4 冲突检测（写入时即时）

新 pending 入库前：
1. 计算新 entry embedding
2. 在 `atrium_vec` 查 active 条目 top 3（cosine > 0.7）
3. 对每个候选 LLM 判 EQUIVALENT / CONTRADICT / UNRELATED
4. `CONTRADICT` → `has_conflict_with=old_id`，review 时特别提示
5. `EQUIVALENT` → 不新建，追加 evidence 到旧条目，`last_confirmed_at=now()`

### 6.5 审计（changelog）

所有状态/内容变更都写 `atrium_changelog`。`audit` CLI 输出：
- needs_review 列表
- 最近 7 天 auto-archived
- 最近冲突

### 6.6 低频 Telegram 通知（单向）

- 默认**每周**摘要：`有 N 条条目被降级，M 条归档`
- 仅在确有变化时推送
- 用户可 `/memory_notify off` 关

---

## 7. 集成、异常、测试

### 7.1 `mybot/palace/` 模块结构

```
mybot/palace/
├── __init__.py               (公开接口 MemoryPalace)
├── store.py                  (SQLite + vec + fts CRUD)
├── writer.py                 (archive_session 编排)
├── chunker.py                (LLM 切子话题 / 摘要)
├── router.py                 (房间路由 + 溢出合并)
├── retriever.py              (混合检索管线)
├── embedder.py               (bge-m3 加载与调用)
├── reranker.py               (bge-reranker-v2-m3)
├── atrium.py                 (中庭读写 + 注入 + 冲突检测)
├── inspector.py              (30 天巡检 daemon)
├── proposer.py               (inferred 候选提案 nightly)
├── cli.py                    (memory review/list/... 子命令)
├── tool_palace.py            (BaseTool: palace 工具)
└── migrations/
    └── 001_init.sql          (初始 schema + 触发器)
```

### 7.2 启动流程

```
python -m mybot 启动:
  1. 加载 bge-m3 模型到内存（~800MB, 5s）
  2. 打开 palace.db；若首次运行则跑 migrations/001_init.sql
  3. 预热中庭（缓存所有 rule + preference active 条目）
  4. Agent 就绪
```

### 7.3 异常分级

| 级别 | 场景 | 行为 |
|---|---|---|
| P0 致命 | palace.db 损坏且无备份 | 启动 abort，Telegram 告警 |
| P1 降级 | sqlite-vec 缺 / bge-m3 缺 | BM25-only，warn |
| P2 局部 | archive_session LLM 超时 | session 缓存 `data/pending_sessions/`，下次重试 |
| P3 容忍 | 某抽屉嵌入失败 | 照写 south_drawer，后台 daemon 补 |
| P4 记录 | 巡检某条裁判失败 | 跳过，下周期重试 |

**核心原则**：记忆系统挂了不能挂 agent。

### 7.4 备份

Nightly 2 AM daemon：
- `palace.db` 复制到 `data/backups/palace.db.YYYYMMDD`（保留 30 天）
- 每月 1 日归档到 iCloud `~/Library/Mobile Documents/com~apple~CloudDocs/mybot-palace/`

手动：`python -m mybot memory backup now`

### 7.5 测试三层

**层 1: 单元（pytest）**—每模块 ≥5 用例，重点：
- `store.py`: schema、CRUD、年份塔自动创建
- `chunker.py`: 单话题/多话题/空 session
- `router.py`: 固定房/动态房/溢出合并
- `retriever.py`: RRF 正确性、rerank 降级、scope 过滤
- `atrium.py`: **黑名单硬拦截、冲突检测、分层注入**

防铁锈专项：
```python
def test_atrium_rejects_tool_failure_narratives():
    entry = palace.atrium.propose("本体论服务端口 8003 不可用")
    assert entry is None  # 黑名单拦下
```

**层 2: 集成** — fixture session 端到端
```python
async def test_archive_retrieve_round_trip():
    session = load_fixture("beijing_spending_session.json")
    await palace.archive_session("test", session)
    ctx = await palace.assemble_context("我在北京花了多少钱")
    assert "北京" in ctx
    assert "不可用" not in ctx  # 未污染
```

**层 3: E2E smoke** — `scripts/smoke_palace_no_rust.py`
复现今天的 bug：
1. 模拟上午"8003 不可用"的失败 session 归档
2. 用新 session 问相同问题
3. 断言：中庭无"不可用"条目，召回是历史真实消费讨论

---

## 8. 迁移 & 回滚

### 8.1 迁移（清零重启）

```bash
# 1. 备份旧 DB
cp data/memory.db data/memory.db.legacy-20260416.bak

# 2. 新建 palace.db
python -m mybot memory init

# 3. 验证
python -m mybot memory stats
# 北塔 0, 南塔 0, 中庭 0, 系统就绪

# 4. 重启 mybot
launchctl kickstart -k gui/$(id -u)/com.xingzq.mybot

# 5. Telegram 发消息验证
```

### 8.2 回滚

若上线出问题：
```bash
launchctl unload ~/Library/LaunchAgents/com.xingzq.mybot.plist
mv data/memory.db.legacy-20260416.bak data/memory.db
git revert <palace-merge-commit>
launchctl load ~/Library/LaunchAgents/com.xingzq.mybot.plist
```

**决胜点**：新系统与旧系统在 git 不同提交，互不污染；回滚 = `git revert`。

---

## 9. 开放问题 / 未来工作

- **Year-tower 可视化 UI**：把某年塔的 365×20×20 画成平面图（Web 或 TUI），直接点进抽屉看原文
- **主动记忆回溯**：`mybot recall "上周我聊过 X"` 命令直接跑检索返回 top 5
- **跨用户/多用户**：目前单用户，如要支持多账户 Telegram，需加 `user_id` 分区（每人一套塔）
- **向量模型升级**：bge-m3 → Qwen3-Embedding（需保证维度兼容或重建索引）
- **非对话记忆源**：从 myontology、neural-twin、照片语义等系统也写入南塔？（需在 chunker 里加 source 字段，当前只支持 conversation）
- **归档后的再聚合**：10 年后，可否把老塔按年摘要浓缩？需另写 `memory compress` 工具

---

## 10. 决策摘要（10 条澄清）

| # | 问题 | 决定 |
|---|---|---|
| 1 | 首要目的 | 全都要（A+B+C+D+E） |
| 2 | 加入中庭？ | 是 |
| 3 | 中庭命名 | **中庭**（丽泽 SOHO 实景对应） |
| 4 | 抽屉颗粒度 | **C 子话题** |
| 5 | 房间主题分配 | **C 混合**（前 10 固定 + 后 10 动态） |
| 6 | 365 天后 | **C 年份堆叠**（园区式） |
| 7 | 中庭写入门槛 | **D 混合**（黑名单 + 显式 + 证据链 + 巡检） |
| 8 | 检索技术 | 修正为 **bge-m3 本地 + FTS5 + bge-reranker-v2-m3**（单一维度） |
| 9 | 容量溢出 | **C 合并最相似** |
| 10 | 迁移策略 | **A 清零重启** |

子决策：
- 中庭注入：rule+pref 全注入，fact top 3
- get_raw_conversation：作为 tool 暴露
- CLI 审批，不走 Telegram 交互
- 巡检降级通知：每周 Telegram 单向推送
- `require_manual_approve=true`：pending 不自动转 active

---

**Spec 结束。待用户复审后交 writing-plans skill 产出实施计划。**
