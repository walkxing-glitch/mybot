# Evolution Engine Phase 1: Queue + Heartbeat + Chat Event

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the evolution infrastructure — SQLite queue, heartbeat loop, and chat_event data collection — so Phase 2-4 (SkillForge, Mirror, Scout) have a foundation to build on.

**Architecture:** New `mybot/evolution/` package with three modules: `queue.py` (SQLite CRUD for evolution_queue), `heartbeat.py` (async tick loop that defers during active conversations), and integration points in `Agent.chat()` to emit chat_events. Config extended with `HeartbeatConfig` dataclass.

**Tech Stack:** Python 3.12, asyncio, aiosqlite, pytest-asyncio

---

## File Structure

| Action | File | Responsibility |
|--------|------|---------------|
| Create | `mybot/evolution/__init__.py` | Package init |
| Create | `mybot/evolution/queue.py` | `EvolutionQueue` class — SQLite schema + CRUD |
| Create | `mybot/evolution/heartbeat.py` | `HeartbeatLoop` class — async tick loop |
| Create | `tests/test_evolution_queue.py` | Queue unit tests |
| Create | `tests/test_heartbeat.py` | Heartbeat unit tests |
| Create | `tests/test_chat_event.py` | Chat event integration test |
| Modify | `mybot/config.py` | Add `HeartbeatConfig` dataclass |
| Modify | `mybot/agent.py:100-138` | Emit chat_event after each chat turn |
| Modify | `mybot/agent.py:203-252` | Return tool_log from `_run_tool_loop` |
| Modify | `mybot/agent.py:254-291` | Collect tool execution metadata in `_dispatch_tool_calls` |
| Modify | `mybot/gateway/telegram.py` | Start heartbeat on startup |
| Modify | `config.yaml` | Add `heartbeat` section |

---

### Task 1: EvolutionQueue — Schema and Insert

**Files:**
- Create: `mybot/evolution/__init__.py`
- Create: `mybot/evolution/queue.py`
- Test: `tests/test_evolution_queue.py`

- [ ] **Step 1: Write failing test for queue init and insert**

```python
# tests/test_evolution_queue.py
"""Tests for EvolutionQueue."""

import json
import pytest
from mybot.evolution.queue import EvolutionQueue


@pytest.fixture
async def queue(tmp_path):
    q = EvolutionQueue(db_path=tmp_path / "evo.db")
    await q.initialize()
    yield q
    await q.close()


async def test_insert_and_get(queue):
    eid = await queue.insert(
        type="skill",
        source="skillforge",
        payload={"name": "test_skill", "trigger": "test"},
    )
    assert eid  # non-empty string
    item = await queue.get(eid)
    assert item is not None
    assert item["type"] == "skill"
    assert item["source"] == "skillforge"
    assert item["status"] == "proposed"
    assert json.loads(item["payload"])["name"] == "test_skill"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_evolution_queue.py::test_insert_and_get -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mybot.evolution'`

- [ ] **Step 3: Write EvolutionQueue with init, insert, get**

```python
# mybot/evolution/__init__.py
"""Evolution engine — heartbeat-driven self-improvement."""
```

```python
# mybot/evolution/queue.py
"""EvolutionQueue — SQLite storage for evolution proposals and chat events."""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import aiosqlite

logger = logging.getLogger(__name__)

_SCHEMA = """\
CREATE TABLE IF NOT EXISTS evolution_queue (
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
CREATE INDEX IF NOT EXISTS idx_evo_status ON evolution_queue(status);
CREATE INDEX IF NOT EXISTS idx_evo_type ON evolution_queue(type, status);
CREATE INDEX IF NOT EXISTS idx_evo_source ON evolution_queue(source);
"""


class EvolutionQueue:
    def __init__(self, db_path: str | Path = "data/evolution.db"):
        self.db_path = Path(db_path)
        self._db: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._db = await aiosqlite.connect(str(self.db_path))
        self._db.row_factory = aiosqlite.Row
        await self._db.executescript(_SCHEMA)
        await self._db.commit()

    async def close(self) -> None:
        if self._db:
            await self._db.close()
            self._db = None

    async def insert(
        self,
        *,
        type: str,
        source: str,
        payload: dict[str, Any],
        priority: int = 0,
        expires_in_days: int = 30,
    ) -> str:
        eid = uuid.uuid4().hex[:12]
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)
        await self._db.execute(
            "INSERT INTO evolution_queue (id, type, source, status, priority, payload, expires_at) "
            "VALUES (?, ?, ?, 'proposed', ?, ?, ?)",
            (eid, type, source, priority, json.dumps(payload, ensure_ascii=False), expires_at.isoformat()),
        )
        await self._db.commit()
        return eid

    async def get(self, eid: str) -> dict[str, Any] | None:
        async with self._db.execute(
            "SELECT * FROM evolution_queue WHERE id = ?", (eid,)
        ) as cursor:
            row = await cursor.fetchone()
            return dict(row) if row else None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_evolution_queue.py::test_insert_and_get -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mybot/evolution/__init__.py mybot/evolution/queue.py tests/test_evolution_queue.py
git commit -m "feat(evolution): EvolutionQueue with schema, insert, get"
```

---

### Task 2: EvolutionQueue — List, Update Status, Expire

**Files:**
- Modify: `mybot/evolution/queue.py`
- Test: `tests/test_evolution_queue.py`

- [ ] **Step 1: Write failing tests**

Append to `tests/test_evolution_queue.py`:

```python
async def test_list_by_status(queue):
    await queue.insert(type="skill", source="skillforge", payload={"a": 1})
    await queue.insert(type="skill", source="skillforge", payload={"b": 2})
    items = await queue.list_by_status("proposed")
    assert len(items) == 2


async def test_list_by_type_and_status(queue):
    await queue.insert(type="skill", source="skillforge", payload={"a": 1})
    await queue.insert(type="prompt_tweak", source="mirror", payload={"b": 2})
    skills = await queue.list_by_status("proposed", type="skill")
    assert len(skills) == 1
    assert skills[0]["type"] == "skill"


async def test_update_status(queue):
    eid = await queue.insert(type="skill", source="skillforge", payload={})
    await queue.update_status(eid, "approved")
    item = await queue.get(eid)
    assert item["status"] == "approved"
    assert item["reviewed_at"] is not None


async def test_update_status_applied(queue):
    eid = await queue.insert(type="skill", source="skillforge", payload={})
    await queue.update_status(eid, "applied")
    item = await queue.get(eid)
    assert item["status"] == "applied"
    assert item["applied_at"] is not None


async def test_expire_old_proposals(queue):
    eid = await queue.insert(type="skill", source="skillforge", payload={}, expires_in_days=-1)
    expired_count = await queue.expire_stale()
    assert expired_count == 1
    item = await queue.get(eid)
    assert item["status"] == "expired"


async def test_expire_skips_fresh(queue):
    await queue.insert(type="skill", source="skillforge", payload={}, expires_in_days=30)
    expired_count = await queue.expire_stale()
    assert expired_count == 0


async def test_delete_old_chat_events(queue):
    eid = await queue.insert(type="chat_event", source="agent", payload={}, expires_in_days=-1)
    deleted = await queue.cleanup_chat_events(max_age_days=0)
    assert deleted == 1
    item = await queue.get(eid)
    assert item is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python3 -m pytest tests/test_evolution_queue.py -v`
Expected: FAIL — `AttributeError: 'EvolutionQueue' object has no attribute 'list_by_status'`

- [ ] **Step 3: Implement list_by_status, update_status, expire_stale, cleanup_chat_events**

Add to `mybot/evolution/queue.py` `EvolutionQueue` class:

```python
    async def list_by_status(
        self, status: str, *, type: str | None = None, limit: int = 50,
    ) -> list[dict[str, Any]]:
        if type:
            sql = "SELECT * FROM evolution_queue WHERE status = ? AND type = ? ORDER BY priority DESC, created_at DESC LIMIT ?"
            params = (status, type, limit)
        else:
            sql = "SELECT * FROM evolution_queue WHERE status = ? ORDER BY priority DESC, created_at DESC LIMIT ?"
            params = (status, limit)
        async with self._db.execute(sql, params) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def update_status(self, eid: str, new_status: str) -> None:
        now = datetime.now(timezone.utc).isoformat()
        if new_status == "applied":
            await self._db.execute(
                "UPDATE evolution_queue SET status = ?, applied_at = ?, reviewed_at = COALESCE(reviewed_at, ?) WHERE id = ?",
                (new_status, now, now, eid),
            )
        elif new_status in ("approved", "rejected"):
            await self._db.execute(
                "UPDATE evolution_queue SET status = ?, reviewed_at = ? WHERE id = ?",
                (new_status, now, eid),
            )
        else:
            await self._db.execute(
                "UPDATE evolution_queue SET status = ? WHERE id = ?",
                (new_status, eid),
            )
        await self._db.commit()

    async def expire_stale(self) -> int:
        now = datetime.now(timezone.utc).isoformat()
        cursor = await self._db.execute(
            "UPDATE evolution_queue SET status = 'expired' WHERE status = 'proposed' AND expires_at < ?",
            (now,),
        )
        await self._db.commit()
        return cursor.rowcount

    async def cleanup_chat_events(self, max_age_days: int = 30) -> int:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=max_age_days)).isoformat()
        cursor = await self._db.execute(
            "DELETE FROM evolution_queue WHERE type = 'chat_event' AND created_at < ?",
            (cutoff,),
        )
        await self._db.commit()
        return cursor.rowcount
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_evolution_queue.py -v`
Expected: All 8 tests PASS

- [ ] **Step 5: Commit**

```bash
git add mybot/evolution/queue.py tests/test_evolution_queue.py
git commit -m "feat(evolution): queue list, status update, expire, cleanup"
```

---

### Task 3: EvolutionQueue — Insert Chat Event (convenience method)

**Files:**
- Modify: `mybot/evolution/queue.py`
- Test: `tests/test_evolution_queue.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_evolution_queue.py`:

```python
async def test_insert_chat_event(queue):
    eid = await queue.insert_chat_event(
        session_id="tg-12345",
        tool_calls=[
            {"name": "palace", "success": True, "latency_ms": 230},
            {"name": "web_search", "success": False, "latency_ms": 5100},
        ],
        memory_hit=True,
        negative_signal=False,
        turn_count=4,
    )
    item = await queue.get(eid)
    assert item["type"] == "chat_event"
    assert item["source"] == "agent"
    payload = json.loads(item["payload"])
    assert payload["session_id"] == "tg-12345"
    assert len(payload["tool_calls"]) == 2
    assert payload["memory_hit"] is True
    assert payload["turn_count"] == 4
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_evolution_queue.py::test_insert_chat_event -v`
Expected: FAIL — `AttributeError: 'EvolutionQueue' object has no attribute 'insert_chat_event'`

- [ ] **Step 3: Implement insert_chat_event**

Add to `mybot/evolution/queue.py` `EvolutionQueue` class:

```python
    async def insert_chat_event(
        self,
        *,
        session_id: str,
        tool_calls: list[dict[str, Any]],
        memory_hit: bool,
        negative_signal: bool,
        turn_count: int,
    ) -> str:
        payload = {
            "session_id": session_id,
            "tool_calls": tool_calls,
            "memory_hit": memory_hit,
            "negative_signal": negative_signal,
            "turn_count": turn_count,
        }
        return await self.insert(
            type="chat_event",
            source="agent",
            payload=payload,
            expires_in_days=30,
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python3 -m pytest tests/test_evolution_queue.py::test_insert_chat_event -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mybot/evolution/queue.py tests/test_evolution_queue.py
git commit -m "feat(evolution): insert_chat_event convenience method"
```

---

### Task 4: HeartbeatConfig

**Files:**
- Modify: `mybot/config.py`
- Test: `tests/test_evolution_queue.py` (reuse file, add config test)

- [ ] **Step 1: Write failing test**

Create `tests/test_config_heartbeat.py`:

```python
# tests/test_config_heartbeat.py
"""Tests for HeartbeatConfig loading."""

from mybot.config import Config


def test_heartbeat_config_defaults():
    cfg = Config.from_dict({})
    assert cfg.heartbeat.enabled is False
    assert cfg.heartbeat.interval_seconds == 1800
    assert cfg.heartbeat.mirror_interval_hours == 24
    assert cfg.heartbeat.scout_interval_hours == 168


def test_heartbeat_config_from_yaml():
    cfg = Config.from_dict({
        "heartbeat": {
            "enabled": True,
            "interval_seconds": 900,
            "mirror": {"interval_hours": 12, "enabled": True},
            "scout": {"interval_hours": 72, "enabled": False},
            "skill_forge": {"enabled": True},
        }
    })
    assert cfg.heartbeat.enabled is True
    assert cfg.heartbeat.interval_seconds == 900
    assert cfg.heartbeat.mirror_interval_hours == 12
    assert cfg.heartbeat.scout_interval_hours == 72
    assert cfg.heartbeat.mirror_enabled is True
    assert cfg.heartbeat.scout_enabled is False
    assert cfg.heartbeat.skill_forge_enabled is True
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_config_heartbeat.py -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'heartbeat'`

- [ ] **Step 3: Add HeartbeatConfig to config.py**

Add after `GatewayConfig` in `mybot/config.py`:

```python
@dataclass
class HeartbeatConfig:
    enabled: bool = False
    interval_seconds: int = 1800
    skill_forge_enabled: bool = True
    mirror_enabled: bool = True
    mirror_interval_hours: int = 24
    scout_enabled: bool = True
    scout_interval_hours: int = 168
    max_llm_calls_per_tick: int = 10
```

Add `heartbeat: HeartbeatConfig` field to `Config` dataclass:

```python
@dataclass
class Config:
    model: ModelConfig
    api_keys: dict[str, str]
    tools: ToolsConfig
    memory: MemoryConfig
    gateway: GatewayConfig
    heartbeat: HeartbeatConfig
    raw: dict[str, Any]
```

Add parsing in `Config.from_dict()`, before the final `return cls(...)`:

```python
        hb_data = data.get("heartbeat", {}) or {}
        mirror_data = hb_data.get("mirror", {}) or {}
        scout_data = hb_data.get("scout", {}) or {}
        sf_data = hb_data.get("skill_forge", {}) or {}
        heartbeat = HeartbeatConfig(
            enabled=bool(hb_data.get("enabled", False)),
            interval_seconds=int(hb_data.get("interval_seconds", 1800)),
            skill_forge_enabled=bool(sf_data.get("enabled", True)),
            mirror_enabled=bool(mirror_data.get("enabled", True)),
            mirror_interval_hours=int(mirror_data.get("interval_hours", 24)),
            scout_enabled=bool(scout_data.get("enabled", True)),
            scout_interval_hours=int(scout_data.get("interval_hours", 168)),
            max_llm_calls_per_tick=int(hb_data.get("max_llm_calls_per_tick", 10)),
        )
```

Add `heartbeat=heartbeat` to the `return cls(...)` call.

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_config_heartbeat.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add mybot/config.py tests/test_config_heartbeat.py
git commit -m "feat(evolution): HeartbeatConfig dataclass"
```

---

### Task 5: HeartbeatLoop — Core Tick Loop

**Files:**
- Create: `mybot/evolution/heartbeat.py`
- Test: `tests/test_heartbeat.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_heartbeat.py
"""Tests for HeartbeatLoop."""

import asyncio
import pytest
from unittest.mock import AsyncMock
from mybot.evolution.heartbeat import HeartbeatLoop
from mybot.config import HeartbeatConfig


@pytest.fixture
def config():
    return HeartbeatConfig(enabled=True, interval_seconds=0.1)


async def test_heartbeat_ticks(config):
    on_tick = AsyncMock()
    loop = HeartbeatLoop(config=config, on_tick=on_tick)
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.35)
    loop.stop()
    await task
    assert on_tick.call_count >= 2


async def test_heartbeat_defers_when_busy(config):
    on_tick = AsyncMock()
    loop = HeartbeatLoop(config=config, on_tick=on_tick)
    loop.set_busy(True)
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.25)
    assert on_tick.call_count == 0
    loop.set_busy(False)
    await asyncio.sleep(0.15)
    loop.stop()
    await task
    assert on_tick.call_count >= 1


async def test_heartbeat_disabled():
    config = HeartbeatConfig(enabled=False)
    on_tick = AsyncMock()
    loop = HeartbeatLoop(config=config, on_tick=on_tick)
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.2)
    loop.stop()
    await task
    assert on_tick.call_count == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_heartbeat.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'mybot.evolution.heartbeat'`

- [ ] **Step 3: Implement HeartbeatLoop**

```python
# mybot/evolution/heartbeat.py
"""HeartbeatLoop — async tick loop for evolution subsystems."""

from __future__ import annotations

import asyncio
import logging
from typing import Any, Awaitable, Callable

from mybot.config import HeartbeatConfig

logger = logging.getLogger(__name__)


class HeartbeatLoop:
    def __init__(
        self,
        *,
        config: HeartbeatConfig,
        on_tick: Callable[[], Awaitable[Any]],
    ) -> None:
        self._config = config
        self._on_tick = on_tick
        self._busy = False
        self._running = False

    def set_busy(self, busy: bool) -> None:
        self._busy = busy

    def stop(self) -> None:
        self._running = False

    async def run(self) -> None:
        if not self._config.enabled:
            return
        self._running = True
        logger.info(
            "Heartbeat started, interval=%ss", self._config.interval_seconds
        )
        while self._running:
            await asyncio.sleep(self._config.interval_seconds)
            if not self._running:
                break
            if self._busy:
                logger.debug("Heartbeat tick skipped — agent busy")
                continue
            try:
                await self._on_tick()
            except Exception:
                logger.exception("Heartbeat tick failed")
        logger.info("Heartbeat stopped")
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_heartbeat.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Commit**

```bash
git add mybot/evolution/heartbeat.py tests/test_heartbeat.py
git commit -m "feat(evolution): HeartbeatLoop with defer-when-busy"
```

---

### Task 6: Agent — Collect Tool Log from _run_tool_loop

**Files:**
- Modify: `mybot/agent.py:203-291`
- Test: `tests/test_chat_event.py`

- [ ] **Step 1: Write failing test**

```python
# tests/test_chat_event.py
"""Tests for chat_event emission from Agent."""

import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from mybot.agent import Agent
from mybot.tools.base import BaseTool, ToolResult


class FakeTool(BaseTool):
    name = "fake"
    description = "test tool"
    parameters = {"type": "object", "properties": {"x": {"type": "string"}}, "required": []}

    async def execute(self, **params):
        return ToolResult(success=True, output="ok")


async def test_tool_log_collected():
    """Agent._run_tool_loop should return (text, tool_log)."""
    agent = Agent(config=None, memory_engine=None, tools=[FakeTool()])

    # Mock LLM: first call returns tool_call, second returns final text
    call_count = 0
    async def mock_completion(messages, tools=None, model=None, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "choices": [{"message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "fake", "arguments": "{}"},
                    }],
                }}],
            }
        return {
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
        }

    with patch("mybot.agent.completion", side_effect=mock_completion):
        session = agent._get_or_create_session("test")
        session.append({"role": "user", "content": "hi"})
        text, tool_log = await agent._run_tool_loop(session, "system prompt")

    assert text == "done"
    assert len(tool_log) == 1
    assert tool_log[0]["name"] == "fake"
    assert tool_log[0]["success"] is True
    assert "latency_ms" in tool_log[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_chat_event.py::test_tool_log_collected -v`
Expected: FAIL — `_run_tool_loop` returns str, not tuple

- [ ] **Step 3: Modify _run_tool_loop and _dispatch_tool_calls to collect tool_log**

In `mybot/agent.py`, change `_run_tool_loop` signature and body:

Replace the method `_run_tool_loop` (lines 203-252) with:

```python
    async def _run_tool_loop(
        self,
        session: SessionState,
        system_prompt: str,
    ) -> tuple[str, list[dict[str, Any]]]:
        """Run LLM <-> tool loop. Returns (final_text, tool_log)."""
        tools_schema = self._build_tools_schema()
        tool_log: list[dict[str, Any]] = []

        for iteration in range(MAX_TOOL_ITERATIONS):
            messages = self._assemble_messages(session, system_prompt)
            logger.debug(
                "llm call: session=%s iter=%d messages=%d tools=%d",
                session.session_id,
                iteration,
                len(messages),
                len(tools_schema),
            )

            response = await completion(
                messages=messages,
                tools=tools_schema or None,
                model=self.model,
            )

            assistant_message = self._extract_assistant_message(response)
            session.append(assistant_message)

            tool_calls = assistant_message.get("tool_calls") or []
            if not tool_calls:
                content = assistant_message.get("content") or ""
                if isinstance(content, list):
                    content = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in content
                    )
                return ((content or "").strip() or "(empty)", tool_log)

            round_log = await self._dispatch_tool_calls(session, tool_calls)
            tool_log.extend(round_log)

        logger.warning(
            "tool loop hit MAX_TOOL_ITERATIONS=%d for session=%s",
            MAX_TOOL_ITERATIONS,
            session.session_id,
        )
        fallback = "(tool call limit reached)"
        session.append({"role": "assistant", "content": fallback})
        return (fallback, tool_log)
```

Replace `_dispatch_tool_calls` (lines 254-291) with:

```python
    async def _dispatch_tool_calls(
        self,
        session: SessionState,
        tool_calls: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Run all tool_calls in parallel. Returns tool_log entries."""

        async def run_one(call: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
            call_id = call.get("id") or ""
            fn = call.get("function") or {}
            name = fn.get("name", "")
            raw_args = fn.get("arguments", "{}")
            try:
                if isinstance(raw_args, str):
                    args = json.loads(raw_args) if raw_args.strip() else {}
                elif isinstance(raw_args, dict):
                    args = raw_args
                else:
                    args = {}
            except json.JSONDecodeError as exc:
                msg = {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": name,
                    "content": f"[tool_error] JSON parse failed: {exc}; raw: {raw_args!r}",
                }
                log_entry = {"name": name, "success": False, "latency_ms": 0}
                return (msg, log_entry)

            t0 = time.monotonic()
            output = await self._execute_tool(name, arguments=args)
            latency_ms = int((time.monotonic() - t0) * 1000)
            success = not output.startswith("[tool_error]")

            msg = {
                "role": "tool",
                "tool_call_id": call_id,
                "name": name,
                "content": output,
            }
            log_entry = {"name": name, "success": success, "latency_ms": latency_ms}
            return (msg, log_entry)

        results = await asyncio.gather(*(run_one(c) for c in tool_calls))
        tool_log = []
        for msg, log_entry in results:
            session.append(msg)
            tool_log.append(log_entry)
        return tool_log
```

Update `chat()` method to handle the new tuple return. Change lines 122-127 from:

```python
            try:
                final_text = await self._run_tool_loop(session, system_prompt)
            except Exception as exc:
                logger.exception("agent tool loop failed: %s", exc)
                final_text = f"抱歉，处理你的请求时出错了：{exc}"
                session.append({"role": "assistant", "content": final_text})
```

To:

```python
            tool_log: list[dict[str, Any]] = []
            try:
                final_text, tool_log = await self._run_tool_loop(session, system_prompt)
            except Exception as exc:
                logger.exception("agent tool loop failed: %s", exc)
                final_text = f"抱歉，处理你的请求时出错了：{exc}"
                session.append({"role": "assistant", "content": final_text})
```

Also add `import time` at the top of agent.py (already imported).

- [ ] **Step 4: Run tests to verify they pass**

Run: `python3 -m pytest tests/test_chat_event.py tests/test_palace_client.py -v`
Expected: All PASS (including existing tests — verify no regression)

- [ ] **Step 5: Commit**

```bash
git add mybot/agent.py tests/test_chat_event.py
git commit -m "refactor(agent): _run_tool_loop returns (text, tool_log)"
```

---

### Task 7: Agent — Emit chat_event to EvolutionQueue

**Files:**
- Modify: `mybot/agent.py:76-83,100-138`
- Test: `tests/test_chat_event.py`

- [ ] **Step 1: Write failing test**

Append to `tests/test_chat_event.py`:

```python
async def test_chat_emits_chat_event(tmp_path):
    """Agent.chat() should write a chat_event to the evolution queue."""
    from mybot.evolution.queue import EvolutionQueue

    queue = EvolutionQueue(db_path=tmp_path / "evo.db")
    await queue.initialize()

    agent = Agent(config=None, memory_engine=None, tools=[FakeTool()])
    agent.evolution_queue = queue

    call_count = 0
    async def mock_completion(messages, tools=None, model=None, **kw):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return {
                "choices": [{"message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{
                        "id": "tc1",
                        "type": "function",
                        "function": {"name": "fake", "arguments": "{}"},
                    }],
                }}],
            }
        return {
            "choices": [{"message": {"role": "assistant", "content": "reply"}}],
        }

    with patch("mybot.agent.completion", side_effect=mock_completion):
        result = await agent.chat("test-session", "hello")

    assert result == "reply"

    # Give the background task a moment to complete
    await asyncio.sleep(0.1)

    events = await queue.list_by_status("proposed", type="chat_event")
    assert len(events) == 1
    payload = json.loads(events[0]["payload"])
    assert payload["session_id"] == "test-session"
    assert len(payload["tool_calls"]) == 1
    assert payload["tool_calls"][0]["name"] == "fake"

    await queue.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python3 -m pytest tests/test_chat_event.py::test_chat_emits_chat_event -v`
Expected: FAIL — Agent doesn't use evolution_queue yet

- [ ] **Step 3: Integrate evolution_queue into Agent.chat()**

In `mybot/agent.py`, add `evolution_queue` to `__init__`:

```python
    def __init__(
        self,
        config: "Config | dict[str, Any] | None",
        memory_engine: "MemoryEngine | None",
        tools: list[BaseTool] | None = None,
        *,
        model: str | None = None,
        evolution_queue: Any | None = None,
    ) -> None:
        self.config = config
        self.memory_engine = memory_engine
        self.tools: list[BaseTool] = list(tools or [])
        self._tool_by_name: dict[str, BaseTool] = {t.name: t for t in self.tools}
        self._sessions: dict[str, SessionState] = {}
        self._session_lock: defaultdict[str, asyncio.Lock] = defaultdict(asyncio.Lock)
        self.evolution_queue = evolution_queue

        if model is not None:
            self.model = model
        else:
            self.model = self._resolve_default_model(config)
```

In `chat()`, after `session.trim()` and before the memory post-processing block, add chat_event emission:

```python
            # 6) Emit chat_event to evolution queue
            if self.evolution_queue is not None:
                memory_hit = bool(memory_context)
                negative_signal = self._detect_negative_signal(message)
                asyncio.create_task(
                    self._emit_chat_event(
                        session_id=session_id,
                        tool_log=tool_log,
                        memory_hit=memory_hit,
                        negative_signal=negative_signal,
                        turn_count=session.turn_count,
                    )
                )

            # 7) Async memory post-processing
```

Renumber the existing memory block comment from 6) to 7).

Add the helper methods to the Agent class:

```python
    _NEGATIVE_PATTERNS = ("不对", "错了", "重来", "算了", "不是这个", "搞错")

    def _detect_negative_signal(self, message: str) -> bool:
        return any(p in message for p in self._NEGATIVE_PATTERNS)

    async def _emit_chat_event(
        self,
        *,
        session_id: str,
        tool_log: list[dict[str, Any]],
        memory_hit: bool,
        negative_signal: bool,
        turn_count: int,
    ) -> None:
        try:
            await self.evolution_queue.insert_chat_event(
                session_id=session_id,
                tool_calls=tool_log,
                memory_hit=memory_hit,
                negative_signal=negative_signal,
                turn_count=turn_count,
            )
        except Exception:
            logger.warning("Failed to emit chat_event", exc_info=True)
```

- [ ] **Step 4: Run all tests to verify they pass**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add mybot/agent.py tests/test_chat_event.py
git commit -m "feat(evolution): Agent emits chat_event to evolution queue"
```

---

### Task 8: Wire Up — Telegram Gateway Starts Heartbeat

**Files:**
- Modify: `mybot/gateway/telegram.py`
- Modify: `config.yaml`

- [ ] **Step 1: Add heartbeat section to config.yaml**

Append to `config.yaml`:

```yaml

# Evolution Engine — 自动进化系统
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

- [ ] **Step 2: Modify run_telegram_from_config to create queue and heartbeat**

In `mybot/gateway/telegram.py`, find the `run_telegram_from_config()` function. After the agent is created and before `run_telegram()` is called, add:

```python
    # Evolution queue
    evolution_queue = None
    if getattr(config, "heartbeat", None) and config.heartbeat.enabled:
        from mybot.evolution.queue import EvolutionQueue
        evolution_queue = EvolutionQueue()
        await evolution_queue.initialize()
        agent.evolution_queue = evolution_queue

    # Heartbeat
    heartbeat = None
    if evolution_queue and config.heartbeat.enabled:
        from mybot.evolution.heartbeat import HeartbeatLoop

        async def on_tick():
            await evolution_queue.expire_stale()
            await evolution_queue.cleanup_chat_events()
            logger.info("Heartbeat tick: expire + cleanup done")

        heartbeat = HeartbeatLoop(config=config.heartbeat, on_tick=on_tick)
```

In the `run_telegram()` function, after `await updater.start_polling(...)`, start the heartbeat task:

Find where the polling starts and add heartbeat task creation. The heartbeat needs to be started as an asyncio task alongside the polling. Add after the polling start:

```python
    heartbeat_task = None
    if heartbeat is not None:
        heartbeat_task = asyncio.create_task(heartbeat.run())
```

For the busy/idle signaling, wrap the `_on_text` handler's agent.chat call so that heartbeat is set busy during conversations. In the `_on_text` handler, before `agent.chat()`:

```python
        if heartbeat is not None:
            heartbeat.set_busy(True)
```

And after agent.chat() returns (in the finally block or after the call):

```python
        if heartbeat is not None:
            heartbeat.set_busy(False)
```

- [ ] **Step 3: Verify existing tests still pass**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 4: Manual smoke test**

Start the bot: `python3 -m mybot telegram`
Expected output includes: `Heartbeat started, interval=1800s`
Send a test message, verify it responds normally.
Check `data/evolution.db` exists and has the schema.

- [ ] **Step 5: Commit**

```bash
git add mybot/gateway/telegram.py config.yaml
git commit -m "feat(evolution): wire heartbeat + queue into telegram gateway"
```

---

### Task 9: CLI Gateway — Queue Support (no heartbeat)

**Files:**
- Modify: `mybot/gateway/cli.py`

- [ ] **Step 1: Add evolution_queue to CLI gateway**

In `mybot/gateway/cli.py`, find `run_cli_from_config()`. After the agent is created, add queue initialization (same pattern as telegram, but no heartbeat — CLI sessions are short-lived):

```python
    # Evolution queue for chat_event logging
    if getattr(config, "heartbeat", None) and config.heartbeat.enabled:
        from mybot.evolution.queue import EvolutionQueue
        evolution_queue = EvolutionQueue()
        await evolution_queue.initialize()
        agent.evolution_queue = evolution_queue
```

- [ ] **Step 2: Verify tests still pass**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add mybot/gateway/cli.py
git commit -m "feat(evolution): wire evolution queue into CLI gateway"
```

---

### Task 10: Final Integration Test

**Files:**
- Test: `tests/test_heartbeat.py` (add integration test)

- [ ] **Step 1: Write integration test for heartbeat + queue**

Append to `tests/test_heartbeat.py`:

```python
async def test_heartbeat_tick_runs_expire_and_cleanup(tmp_path):
    """Integration: heartbeat tick should expire stale and cleanup old events."""
    from mybot.evolution.queue import EvolutionQueue

    queue = EvolutionQueue(db_path=tmp_path / "evo.db")
    await queue.initialize()

    # Insert a stale proposal (already expired)
    await queue.insert(type="skill", source="test", payload={}, expires_in_days=-1)
    # Insert an old chat_event
    await queue.insert(type="chat_event", source="agent", payload={}, expires_in_days=-1)

    async def on_tick():
        await queue.expire_stale()
        await queue.cleanup_chat_events(max_age_days=0)

    config = HeartbeatConfig(enabled=True, interval_seconds=0.1)
    loop = HeartbeatLoop(config=config, on_tick=on_tick)
    task = asyncio.create_task(loop.run())
    await asyncio.sleep(0.25)
    loop.stop()
    await task

    # Stale proposal should be expired
    proposals = await queue.list_by_status("proposed")
    assert len(proposals) == 0
    expired = await queue.list_by_status("expired")
    assert len(expired) == 1

    # Old chat_event should be deleted
    events = await queue.list_by_status("proposed", type="chat_event")
    assert len(events) == 0

    await queue.close()
```

- [ ] **Step 2: Run all tests**

Run: `python3 -m pytest tests/ -v`
Expected: All PASS

- [ ] **Step 3: Commit**

```bash
git add tests/test_heartbeat.py
git commit -m "test(evolution): integration test for heartbeat + queue"
```

- [ ] **Step 4: Push**

```bash
git push origin main
```
