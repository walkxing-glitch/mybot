# 2026-04-18 · Palace 防锈实测 + soho-twin-towers 设计

## 完成事项

### Palace 防锈三道闸实测
1. **回归测试通过**：test_no_rust.py 4 tests PASSED，test_atrium_guards.py PASSED
2. **基线确认**：北塔17 南塔17 中庭0（干净状态）
3. **Telegram 实测三场景**：
   - 工具故障不污染 ✅ — "我在北京花了多少钱" 工具失败后中庭无垃圾
   - 黑名单拦截 ✅ — "记住：本体论服务不可用" 被双重拦截
   - 正常规则写入 ✅ — "记住：以后别加太多emoji" 入库 [preference|active|explicit]
4. **实测后状态**：北塔32 南塔32 中庭1（仅合法条目）
5. **问题发现**：DeepSeek 上下文溢出（140K > 131K），通过 /reset 清空 session 绕过

### soho-twin-towers 设计
6. **设计文档完成**：`docs/superpowers/specs/2026-04-18-soho-twin-towers-design.md`
7. **关键决策**：
   - 项目名 soho-twin-towers，FastAPI :8004
   - 自带 LLM（litellm + DeepSeek）、Embedder（豆包 API）、SQLite 存储
   - mybot 彻底删除 palace/，换 PalaceClient HTTP client
   - 一步到位迁移

## 未完成

1. **soho-twin-towers 实施计划** — 设计已审阅，待调用 writing-plans 生成实施计划并执行
2. **遗留项 3**：neural_twin tool 对 /anomaly/detect 入参适配
3. **遗留项 4**：Telegram 格式优化
4. **DeepSeek 上下文溢出**：长会话需 session 自动截断机制

## Git 提交
- （本次尚未提交，设计文档和 session log 待提交）
