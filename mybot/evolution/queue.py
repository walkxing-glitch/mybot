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
