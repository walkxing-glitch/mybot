"""SQLite persistence layer for MyBot memory engine.

Uses aiosqlite for async access and SQLite FTS5 for full-text search
over memory content. All public methods are async.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import aiosqlite


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class Memory:
    id: str
    content: str
    memory_type: str  # episode / fact / preference / observation
    created_at: datetime
    last_accessed: datetime
    access_count: int = 0
    salience: float = 0.5
    base_importance: float = 0.5
    tags: list[str] = field(default_factory=list)
    source_session: str | None = None
    temporal_context: str | None = None
    status: str = "active"  # active / dormant / archived

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "Memory":
        return cls(
            id=row["id"],
            content=row["content"],
            memory_type=row["memory_type"],
            created_at=_parse_ts(row["created_at"]),
            last_accessed=_parse_ts(row["last_accessed"]),
            access_count=row["access_count"],
            salience=row["salience"],
            base_importance=row["base_importance"],
            tags=json.loads(row["tags"] or "[]"),
            source_session=row["source_session"],
            temporal_context=row["temporal_context"],
            status=row["status"],
        )

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["created_at"] = self.created_at.isoformat()
        d["last_accessed"] = self.last_accessed.isoformat()
        return d


@dataclass
class ProfileTrait:
    id: str
    dimension: str  # behavior / interest / decision_style / social / focus
    key: str
    value: str
    confidence: float = 0.5
    evidence_count: int = 1
    first_observed: datetime = field(default_factory=datetime.utcnow)
    last_updated: datetime = field(default_factory=datetime.utcnow)
    trend: str = "stable"  # rising / stable / declining

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "ProfileTrait":
        return cls(
            id=row["id"],
            dimension=row["dimension"],
            key=row["key"],
            value=row["value"],
            confidence=row["confidence"],
            evidence_count=row["evidence_count"],
            first_observed=_parse_ts(row["first_observed"]),
            last_updated=_parse_ts(row["last_updated"]),
            trend=row["trend"],
        )


@dataclass
class SessionSummary:
    id: str
    session_id: str
    summary: str
    topics: list[str] = field(default_factory=list)
    created_at: datetime = field(default_factory=datetime.utcnow)
    memory_ids: list[str] = field(default_factory=list)

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "SessionSummary":
        return cls(
            id=row["id"],
            session_id=row["session_id"],
            summary=row["summary"],
            topics=json.loads(row["topics"] or "[]"),
            created_at=_parse_ts(row["created_at"]),
            memory_ids=json.loads(row["memory_ids"] or "[]"),
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse_ts(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if value is None:
        return datetime.utcnow()
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return datetime.utcnow()


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


# ---------------------------------------------------------------------------
# MemoryStore
# ---------------------------------------------------------------------------


class MemoryStore:
    """Async SQLite store for memories, profile traits and session summaries.

    Safe to instantiate without touching the filesystem; call ``initialize()``
    once before any other method.
    """

    SCHEMA = [
        """
        CREATE TABLE IF NOT EXISTS memories (
            id TEXT PRIMARY KEY,
            content TEXT NOT NULL,
            memory_type TEXT NOT NULL,
            created_at TIMESTAMP NOT NULL,
            last_accessed TIMESTAMP NOT NULL,
            access_count INTEGER DEFAULT 0,
            salience REAL NOT NULL,
            base_importance REAL NOT NULL,
            tags TEXT DEFAULT '[]',
            source_session TEXT,
            temporal_context TEXT,
            status TEXT DEFAULT 'active'
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS profile_traits (
            id TEXT PRIMARY KEY,
            dimension TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            confidence REAL DEFAULT 0.5,
            evidence_count INTEGER DEFAULT 1,
            first_observed TIMESTAMP,
            last_updated TIMESTAMP,
            trend TEXT DEFAULT 'stable'
        );
        """,
        """
        CREATE TABLE IF NOT EXISTS session_summaries (
            id TEXT PRIMARY KEY,
            session_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            topics TEXT DEFAULT '[]',
            created_at TIMESTAMP NOT NULL,
            memory_ids TEXT DEFAULT '[]'
        );
        """,
        """
        CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts USING fts5(
            content,
            tags,
            content='memories',
            content_rowid='rowid',
            tokenize='unicode61'
        );
        """,
        """
        CREATE TRIGGER IF NOT EXISTS memories_fts_insert
        AFTER INSERT ON memories BEGIN
            INSERT INTO memories_fts(rowid, content, tags)
            VALUES (new.rowid, new.content, new.tags);
        END;
        """,
        """
        CREATE TRIGGER IF NOT EXISTS memories_fts_delete
        AFTER DELETE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, tags)
            VALUES ('delete', old.rowid, old.content, old.tags);
        END;
        """,
        """
        CREATE TRIGGER IF NOT EXISTS memories_fts_update
        AFTER UPDATE ON memories BEGIN
            INSERT INTO memories_fts(memories_fts, rowid, content, tags)
            VALUES ('delete', old.rowid, old.content, old.tags);
            INSERT INTO memories_fts(rowid, content, tags)
            VALUES (new.rowid, new.content, new.tags);
        END;
        """,
        "CREATE INDEX IF NOT EXISTS idx_memories_status ON memories(status);",
        "CREATE INDEX IF NOT EXISTS idx_memories_type ON memories(memory_type);",
        "CREATE INDEX IF NOT EXISTS idx_memories_salience ON memories(salience);",
        "CREATE INDEX IF NOT EXISTS idx_memories_created ON memories(created_at);",
        "CREATE INDEX IF NOT EXISTS idx_profile_dim_key ON profile_traits(dimension, key);",
        "CREATE INDEX IF NOT EXISTS idx_sessions_sid ON session_summaries(session_id);",
    ]

    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self._initialized = False

    # -- lifecycle ---------------------------------------------------------

    async def initialize(self) -> None:
        """Create the DB file and schema if missing. Idempotent."""
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute("PRAGMA journal_mode = WAL;")
            for stmt in self.SCHEMA:
                await db.execute(stmt)
            await db.commit()
        self._initialized = True

    def _connect(self):
        """Return an aiosqlite context manager with row_factory set on enter."""

        class _Wrapper:
            def __init__(self, path: Path):
                self._path = path
                self._cm = None
                self._db: aiosqlite.Connection | None = None

            async def __aenter__(self) -> aiosqlite.Connection:
                self._cm = aiosqlite.connect(self._path)
                self._db = await self._cm.__aenter__()
                self._db.row_factory = aiosqlite.Row
                await self._db.execute("PRAGMA journal_mode = WAL;")
                return self._db

            async def __aexit__(self, exc_type, exc, tb):
                return await self._cm.__aexit__(exc_type, exc, tb)

        return _Wrapper(self.db_path)

    # -- memories CRUD -----------------------------------------------------

    async def insert_memory(self, memory: Memory) -> str:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO memories (
                    id, content, memory_type, created_at, last_accessed,
                    access_count, salience, base_importance, tags,
                    source_session, temporal_context, status
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    memory.id,
                    memory.content,
                    memory.memory_type,
                    _iso(memory.created_at),
                    _iso(memory.last_accessed),
                    memory.access_count,
                    memory.salience,
                    memory.base_importance,
                    json.dumps(memory.tags, ensure_ascii=False),
                    memory.source_session,
                    memory.temporal_context,
                    memory.status,
                ),
            )
            await db.commit()
        return memory.id

    async def get_memory(self, memory_id: str) -> Memory | None:
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,)
            ) as cursor:
                row = await cursor.fetchone()
                return Memory.from_row(row) if row else None

    async def update_memory(self, memory_id: str, **fields: Any) -> None:
        if not fields:
            return
        for key in ("tags",):
            if key in fields and isinstance(fields[key], (list, dict)):
                fields[key] = json.dumps(fields[key], ensure_ascii=False)
        for key in ("created_at", "last_accessed"):
            if key in fields and isinstance(fields[key], datetime):
                fields[key] = _iso(fields[key])

        set_clause = ", ".join(f"{k} = ?" for k in fields)
        params = list(fields.values()) + [memory_id]
        async with self._connect() as db:
            await db.execute(
                f"UPDATE memories SET {set_clause} WHERE id = ?", params
            )
            await db.commit()

    async def delete_memory(self, memory_id: str) -> None:
        async with self._connect() as db:
            await db.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
            await db.commit()

    async def list_memories(
        self,
        *,
        status: str | None = "active",
        memory_type: str | None = None,
        tags: list[str] | None = None,
        start: datetime | None = None,
        end: datetime | None = None,
        min_salience: float | None = None,
        limit: int = 100,
        order_by: str = "salience DESC",
    ) -> list[Memory]:
        """Flexible query over memories with status/type/tag/time filters."""
        where: list[str] = []
        params: list[Any] = []
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if memory_type is not None:
            where.append("memory_type = ?")
            params.append(memory_type)
        if min_salience is not None:
            where.append("salience >= ?")
            params.append(min_salience)
        if start is not None:
            where.append("created_at >= ?")
            params.append(_iso(start))
        if end is not None:
            where.append("created_at <= ?")
            params.append(_iso(end))
        if tags:
            tag_clauses: list[str] = []
            for tag in tags:
                tag_clauses.append("tags LIKE ?")
                params.append(f'%"{tag}"%')
            where.append("(" + " OR ".join(tag_clauses) + ")")

        sql = "SELECT * FROM memories"
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += f" ORDER BY {order_by} LIMIT ?"
        params.append(limit)

        async with self._connect() as db:
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [Memory.from_row(r) for r in rows]

    async def search_memories(
        self,
        query: str,
        *,
        status: str | None = "active",
        min_salience: float | None = None,
        limit: int = 10,
    ) -> list[Memory]:
        """Full-text search via FTS5, ranked by BM25 then salience."""
        if not query.strip():
            return await self.list_memories(
                status=status, min_salience=min_salience, limit=limit
            )

        fts_query = _sanitize_fts_query(query)

        sql = """
            SELECT m.*, bm25(memories_fts) AS rank
            FROM memories_fts
            JOIN memories m ON m.rowid = memories_fts.rowid
            WHERE memories_fts MATCH ?
        """
        params: list[Any] = [fts_query]

        if status is not None:
            sql += " AND m.status = ?"
            params.append(status)
        if min_salience is not None:
            sql += " AND m.salience >= ?"
            params.append(min_salience)

        sql += " ORDER BY rank ASC, m.salience DESC LIMIT ?"
        params.append(limit)

        async with self._connect() as db:
            try:
                async with db.execute(sql, params) as cursor:
                    rows = await cursor.fetchall()
            except aiosqlite.OperationalError:
                return await self._like_search(
                    query, status=status, min_salience=min_salience, limit=limit
                )
        return [Memory.from_row(r) for r in rows]

    async def _like_search(
        self,
        query: str,
        *,
        status: str | None,
        min_salience: float | None,
        limit: int,
    ) -> list[Memory]:
        sql = "SELECT * FROM memories WHERE content LIKE ?"
        params: list[Any] = [f"%{query}%"]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        if min_salience is not None:
            sql += " AND salience >= ?"
            params.append(min_salience)
        sql += " ORDER BY salience DESC LIMIT ?"
        params.append(limit)

        async with self._connect() as db:
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
        return [Memory.from_row(r) for r in rows]

    async def touch_memory(self, memory_id: str, at: datetime | None = None) -> None:
        """Bump access_count and last_accessed on recall."""
        at = at or datetime.utcnow()
        async with self._connect() as db:
            await db.execute(
                """
                UPDATE memories
                SET access_count = access_count + 1, last_accessed = ?
                WHERE id = ?
                """,
                (_iso(at), memory_id),
            )
            await db.commit()

    async def batch_update_salience(
        self, updates: Iterable[tuple[str, float]]
    ) -> int:
        updates = list(updates)
        if not updates:
            return 0
        async with self._connect() as db:
            await db.executemany(
                "UPDATE memories SET salience = ? WHERE id = ?",
                [(sal, mid) for mid, sal in updates],
            )
            await db.commit()
        return len(updates)

    async def batch_update_status(
        self, updates: Iterable[tuple[str, str]]
    ) -> int:
        updates = list(updates)
        if not updates:
            return 0
        async with self._connect() as db:
            await db.executemany(
                "UPDATE memories SET status = ? WHERE id = ?",
                [(status, mid) for mid, status in updates],
            )
            await db.commit()
        return len(updates)

    async def count_memories(
        self, *, status: str | None = None, memory_type: str | None = None
    ) -> int:
        sql = "SELECT COUNT(*) AS c FROM memories"
        where: list[str] = []
        params: list[Any] = []
        if status is not None:
            where.append("status = ?")
            params.append(status)
        if memory_type is not None:
            where.append("memory_type = ?")
            params.append(memory_type)
        if where:
            sql += " WHERE " + " AND ".join(where)
        async with self._connect() as db:
            async with db.execute(sql, params) as cursor:
                row = await cursor.fetchone()
                return int(row["c"]) if row else 0

    # -- profile traits CRUD ----------------------------------------------

    async def upsert_trait(self, trait: ProfileTrait) -> str:
        """Insert or update a trait keyed by (dimension, key)."""
        async with self._connect() as db:
            async with db.execute(
                "SELECT id FROM profile_traits WHERE dimension = ? AND key = ?",
                (trait.dimension, trait.key),
            ) as cursor:
                row = await cursor.fetchone()

            if row:
                trait.id = row["id"]
                await db.execute(
                    """
                    UPDATE profile_traits
                    SET value = ?, confidence = ?, evidence_count = ?,
                        last_updated = ?, trend = ?
                    WHERE id = ?
                    """,
                    (
                        trait.value,
                        trait.confidence,
                        trait.evidence_count,
                        _iso(trait.last_updated),
                        trait.trend,
                        trait.id,
                    ),
                )
            else:
                await db.execute(
                    """
                    INSERT INTO profile_traits (
                        id, dimension, key, value, confidence,
                        evidence_count, first_observed, last_updated, trend
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        trait.id,
                        trait.dimension,
                        trait.key,
                        trait.value,
                        trait.confidence,
                        trait.evidence_count,
                        _iso(trait.first_observed),
                        _iso(trait.last_updated),
                        trait.trend,
                    ),
                )
            await db.commit()
        return trait.id

    async def get_trait(
        self, dimension: str, key: str
    ) -> ProfileTrait | None:
        async with self._connect() as db:
            async with db.execute(
                "SELECT * FROM profile_traits WHERE dimension = ? AND key = ?",
                (dimension, key),
            ) as cursor:
                row = await cursor.fetchone()
                return ProfileTrait.from_row(row) if row else None

    async def list_traits(
        self, *, dimension: str | None = None, min_confidence: float = 0.0
    ) -> list[ProfileTrait]:
        sql = "SELECT * FROM profile_traits WHERE confidence >= ?"
        params: list[Any] = [min_confidence]
        if dimension:
            sql += " AND dimension = ?"
            params.append(dimension)
        sql += " ORDER BY dimension, confidence DESC"
        async with self._connect() as db:
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [ProfileTrait.from_row(r) for r in rows]

    async def delete_trait(self, trait_id: str) -> None:
        async with self._connect() as db:
            await db.execute(
                "DELETE FROM profile_traits WHERE id = ?", (trait_id,)
            )
            await db.commit()

    # -- session summaries CRUD -------------------------------------------

    async def insert_session_summary(self, summary: SessionSummary) -> str:
        async with self._connect() as db:
            await db.execute(
                """
                INSERT INTO session_summaries (
                    id, session_id, summary, topics, created_at, memory_ids
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    summary.id,
                    summary.session_id,
                    summary.summary,
                    json.dumps(summary.topics, ensure_ascii=False),
                    _iso(summary.created_at),
                    json.dumps(summary.memory_ids, ensure_ascii=False),
                ),
            )
            await db.commit()
        return summary.id

    async def list_session_summaries(
        self, session_id: str | None = None, limit: int = 50
    ) -> list[SessionSummary]:
        sql = "SELECT * FROM session_summaries"
        params: list[Any] = []
        if session_id:
            sql += " WHERE session_id = ?"
            params.append(session_id)
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)
        async with self._connect() as db:
            async with db.execute(sql, params) as cursor:
                rows = await cursor.fetchall()
                return [SessionSummary.from_row(r) for r in rows]

    # -- convenience -------------------------------------------------------

    @staticmethod
    def new_id() -> str:
        return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# FTS5 query sanitation
# ---------------------------------------------------------------------------


_FTS_FORBIDDEN = set('"*:()^~/+-')


def _sanitize_fts_query(q: str) -> str:
    tokens: list[str] = []
    buf: list[str] = []
    for ch in q:
        if ch.isspace():
            if buf:
                tokens.append("".join(buf))
                buf = []
        elif ch in _FTS_FORBIDDEN:
            continue
        else:
            buf.append(ch)
    if buf:
        tokens.append("".join(buf))
    quoted = [f'"{t}"' for t in tokens if t]
    return " OR ".join(quoted) if quoted else '""'
