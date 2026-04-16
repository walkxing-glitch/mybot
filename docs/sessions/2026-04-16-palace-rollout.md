# 2026-04-16 · 丽泽园（Palace）记忆系统上线

**上下文：** 当天上午发现记忆污染 bug（见 `2026-04-16-mybot-v0.1-development.md` 第 14 节）——
LLM 把工具失败叙述（"本体论服务 8003 不可用"）抽取成长期"事实"写进 `data/memory.db`，
修完服务后语义召回把过时错误注入 prompt，agent 继续复读错误。
下午按 `docs/superpowers/plans/2026-04-16-palace-impl-plan.md` 设计 + 实施"丽泽园"新记忆系统，
用架构层面杜绝这类"生锈"。

---

## 1. 最终状态

- **10 个 phase 全部完成**，commit chain：
  ```
  a1d6acf  plan + spec
  757d08a  Phase 0  deps + migration + config + ids
  6baa351  Phase 1  PalaceStore 持久化层
  851cc6b  Phase 2  Embedder + Reranker
  5d3dfba  Phase 3  Chunker/Router/Writer
  f8dd0d1  Phase 4  Retriever + AtriumManager
  aaaf410  Phase 5  MemoryPalace facade + Agent-shim
  4e39da4  Phase 6  PalaceTool
  f0714d5  Phase 7  CLI（init/stats/list/show/review/archive/resurrect/backup）
  f6ad008  Phase 8  anti-rust E2E regression
  [此次]    Phase 10 gateway 接入 + 老 db 备份
  ```
- **测试：** palace 测试 81/81 green（torch 相关的 bge-m3 smoke 通过 `@pytest.mark.slow` 自动跳过）。
- **旧 memory.db 备份：** `data/memory.db.bak-20260416-172942`（下午治理时的备份）+ `data/memory.db.legacy-20260416.bak`（上线前备份）。
- **新 palace.db：** `data/palace.db`（空库，首次消息将触发初始化）。

---

## 2. 架构要点（为何能防"生锈"）

| 关口 | 实现 | 效果 |
|------|------|------|
| SQL 黑名单触发器 | `atrium_insert_blacklist_trigger` BEFORE INSERT ABORT | 含"不可用/未能找到/服务中断/超时/工具报错/无法访问/操作失败/连接失败"的条目**写入即拒** |
| 代码层黑名单 | `Writer._is_blacklisted` 再查一次 | 双保险，不依赖 SQL 层 |
| 显式标记门禁 | `EXPLICIT_MARKERS` = {"记住", "以后别", "永远记住", "我是", "我叫", "我的", "下次"} | Atrium 只收用户**明确授权**的长期事实；工具失败叙述天然没有这些词 |
| 南楼 / 中庭分层 | 南楼 = episodic（按 day-room-drawer 坐标归档，可被检索/复苏/归档）；中庭 = 规则/偏好/事实（只追加、需人工审批） | 工具叙述至多进南楼抽屉（可归档），不会污染长期"事实" |
| Retriever RRF | vec + FTS 双路召回 → RRF 合并 → 可选 reranker | 黑名单在南楼抽屉写入时也会拦，召回结果不会带毒 |

**关键反例测试：** `tests/palace/test_no_rust.py::test_failure_session_does_not_pollute_atrium`
喂 `tests/palace/fixtures/tool_failure_session.json` 整段工具失败对话，archive 完成后 `atrium_entries` 表为空 ✅。

---

## 3. Phase 10 做了什么

### 3.1 Gateway 接入（`mybot/gateway/cli.py` + `telegram.py`）

新增 `_try_build_palace(config)` helper：

1. 读 `config.palace.enabled`，false 直接返回 None（走 legacy MemoryEngine）。
2. 构造 `Embedder(bge-m3, dim=1024)`，**立即用 `encode("palace startup probe")` 探针**。
   - bge-m3 依赖 torch≥2.4，当前环境 torch 2.2.2 会在 `FlagEmbedding` 内部 `nn is not defined` 报错。
   - 探针失败 → `logger.warning` + 返回 None → gateway 自动回退到 MemoryEngine。
3. 探针通过则构造 `Reranker` + `MemoryPalace`，`await palace.initialize()` 建表/触发器。
4. 日志清晰区分："MemoryPalace initialized: %s" vs "MemoryPalace init failed (%s); falling back to legacy engine. Hint: pip install 'torch>=2.4' FlagEmbedding"。

两个 gateway 共享同一个 helper（`telegram.py` 从 `gateway.cli` 导入），保证 CLI / Telegram 行为一致。

### 3.2 Config（`config.yaml` + `mybot/palace/config.py`）

- `config.yaml` 的 `palace:` block 加 `enabled: true`（已存在 db_path / embedder / rooms / atrium_guards）。
- `PalaceConfig` dataclass 新增 `enabled: bool = True` 字段 + `from_dict` 读取逻辑。
- 默认 true，要关闭只需 `enabled: false`。

### 3.3 老 memory.db 备份

- `data/memory.db` → `data/memory.db.legacy-20260416.bak`（bak 文件被 gitignore 排除）。
- 旧 MemoryEngine 在 palace 启动失败时仍会被读取，用备份确保有 fallback 数据源。

---

## 4. 已经 Defer 的 3 项（需下次 session 处理）

### 4.1 torch 升级（阻塞 palace 真正生效）

**现状：** `_try_build_palace` 的探针在当前 venv 必然失败，gateway 实际还在用 legacy MemoryEngine。
**要做：** `pip install 'torch>=2.4' FlagEmbedding` — 首次下载 bge-m3 模型约 2.3GB，建议有线网络时做。
**验证后：** 重启 mybot 服务，日志应出现 "MemoryPalace initialized: data/palace.db"。

### 4.2 launchd 重启 mybot agent

**为什么今晚不做：** launchd 的 `com.xingzq.mybot` 持有 Telegram bot token `@xingzhiqiang_claude_fst_bot`
作长轮询。今晚这个 token 临时被 Claude Code 的"Telegram 桥"占用（用户可以在 Telegram 给我发消息看进度），
重启 launchd 会立刻把桥吃掉。做 4.1 后再同步重启。

### 4.3 v0.2（plan 中明确推迟）

- `mybot/palace/inspector.py`（候选审批 UI 增强）
- `mybot/palace/proposer.py`（自动合并/分类建议）
- `scripts/weekly_palace_report.py`（Telegram 周报推送）
- `mybot memory doctor` CLI（长期记忆体检，见老 session 第 14 节教训）

---

## 5. 测试与验证

```bash
# palace 全量测试
$ pytest tests/palace/ -m "not slow" -q
81 passed, 1 deselected  # test_embedder_bge_m3_smoke 需 torch>=2.4

# 黑名单 SQL 触发器
$ pytest tests/palace/test_no_rust.py -v
test_failure_session_does_not_pollute_atrium PASSED
test_blacklist_phrases_all_eight              PASSED
test_explicit_rule_bypasses_blacklist         PASSED
test_atrium_trigger_raises_on_insert          PASSED

# gateway fallback 验证（torch 2.2.2 环境）
$ python -c "import asyncio, mybot.config, mybot.gateway.cli as g; \
    cfg = mybot.config.Config.load('config.yaml'); \
    print(asyncio.run(g._try_build_palace(cfg)))"
# stderr: MemoryPalace init failed (name 'nn' is not defined); falling back to legacy engine. ...
# stdout: None
```

---

## 6. 下次 session 恢复提示词

> 继续 MyBot 项目。记忆系统架构升级已完成 10 phase（commit `a1d6acf` → 当前），
> 丽泽园南楼/中庭/黑名单三道防线就位，79/79 palace 测试绿。**阻塞项**：bge-m3 需
> `torch≥2.4`（当前 2.2.2），探针失败时 gateway 会自动回退 legacy MemoryEngine，
> 所以生产仍在用老记忆。下次 session 首要两步：(1) `pip install 'torch>=2.4' FlagEmbedding`；
> (2) `launchctl kickstart -k gui/$UID/com.xingzq.mybot` 让新 venv + palace 生效。
> 之后可上 v0.2 三项（inspector / proposer / weekly report）。参考
> `docs/sessions/2026-04-16-palace-rollout.md`。
