# MyBot — Claude Code 项目指令

## 项目概述

个人 AI Agent（Telegram + CLI），通过 HTTP 调用本体论引擎、数字分身和丽泽SOHO双塔DNA记忆系统。

## 本体论引擎接口

MyBot 的 `ontology` 工具对接 myontology/ontology-engine（本机 :8003）。

完整接口文档：`/Users/ddn/Developer/02_AI_Assistants/Claude_Work/myontology/ontology-engine/AGENT_INTERFACE.md`

- Base URL: `http://localhost:8003`
- Ontology ID: `362a5ce1-29ca-4b4b-8bd0-29c122435bd3`
- 27 个操作覆盖：查询、关系、全景、时间、认知（43 种）、图谱、执行（17 个引擎的响应式调度）

## 记忆系统接口

MyBot 的记忆通过 HTTP 调用 soho-twin-towers（本机 :8004）。

- Base URL: `http://localhost:8004`
- API: /session/context, /session/archive, /atrium, /drawers, /stats
- 项目位置: `/Users/ddn/Developer/02_AI_Assistants/Claude_Work/soho-twin-towers/`

## 跨项目原则

- mybot ↔ myontology / neural-twin / soho-twin-towers **永远只 HTTP 调用，不改对方代码**
- ontology tool 只对接 myontology（ontology-platform 已停止开发）
- neural-twin 独立发展，不接本体论

## 技术约束

- 2017 MacBook Pro Intel i7，torch 最高 2.2.2（无 x86_64 macOS wheels for >=2.4）
- 向量嵌入用豆包 API（DoubaoEmbedder，2048 维），不用本地模型
- 本机用 `docker-compose`（非 `docker compose`）
- 避免 brew 安装（慢）

## 测试

```bash
pytest tests/ -m "not slow" -q
```
