# 2026-04-17 · MyBot 增强 + 三项目架构图 + 演示准备

## 完成事项

### MyBot 增强
1. **重命名记忆系统**：丽泽园记忆宫殿 → 丽泽SOHO双塔DNA记忆系统（11 个文件）
2. **Ontology tool 升级 13→27 操作**：新增认知层（summary/by-kind/relationship）、图谱（graph/social-evolution/analytics）、情境（situations/absences）、章节（chapters）、仪表盘（dashboard）、关系深读（relator_synthesis）、对象时间线、响应式管线调度（pipeline/run）
3. **创建 CLAUDE.md**：包含本体论引擎接口引用、跨项目原则、技术约束
4. **修复 DOUBAO_API_KEY 缺失**：.env 里没配 → Palace 回退到 legacy MemoryEngine → 加上 key 后 Palace 恢复
5. **README ontology 描述更新**：13 个操作 → 27 个操作

### 架构图生成
6. **MyBot 架构图更新**：重命名后重新渲染 architecture-core 和 architecture 的 SVG/PNG
7. **MyOntology 架构图**（新）：完整架构（API→Core→Emergence→Pipeline→Object Layer）+ 功能模块图
8. **Neural-Twin 架构图**（新）：完整架构（Data→Feature→Organs→Decision→DNA→API）+ 功能模块图
9. 所有图均通过 kroki.io 渲染，DOT 源文件保存在各项目 docs/ 下

### 系统演示
10. **本体论演示（7 条 Telegram 消息）**：总览、认知层、知识图谱、人生叙事、月度情境、人物档案、架构总览
11. **数字分身演示（5 条）**：13 模型一览、星巴克决策、Apple Vision Pro 决策、批量对比、模型深度数据
12. **记忆系统演示（1 条）**：南楼/北楼/中庭统计 + 防锈三道闸
13. **架构图发送**：6 张架构图 + 功能模块图发到 Telegram

### 数据修复
14. **本体论身份修复**：overview API 返回 name: "Unknown" → 插入 identity semantic 记录 → name: "邢智强"

## 技术细节

### 关键修复
- `.env` 缺 `DOUBAO_API_KEY=6b2b3992-c953-4900-b3fd-861c9a278976` → Palace 初始化失败 `Illegal header value b'Bearer '`
- identity semantic 缺失 → `INSERT INTO semantic ... kind='identity', content='{"name":"邢智强"}'`
- PostgreSQL 连接：`localhost:5433`, user `postgres`, password `changeme`, db `ontology_engine`

### Git 提交
- mybot: `ee6c543` rename 丽泽SOHO, `9056066` ontology 27 ops + CLAUDE.md
- myontology: `5f8d778` architecture + module diagrams
- neural-twin: `eb91316` architecture + module diagrams

## 未完成（后续）

1. **数据滞后**：日历停在 2025-04（差 1 年）、招行 12-31、交行 12-21（差 3.5 月）
2. **forecast/today 缺数据**：日 DNA 只到 2026-04-11，需补采近几天跨模态数据
3. **异常检测入参**：/anomaly/detect 需要 `{data:[...]}` 数组，mybot neural_twin tool 可能未适配
4. **Telegram 格式优化**：表格渲染、图片内联等
5. **记忆防锈实测**：Palace 上线后未在真实对话中验证防锈闸
