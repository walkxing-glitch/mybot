"""PalaceStore: async wrapper around apsw + sqlite-vec.

Design notes:
- apsw is used instead of stdlib sqlite3 because macOS Python.framework
  ships with sqlite3 compiled without `--enable-load-extension`, which
  sqlite-vec requires.
- Each write path takes an asyncio.Lock to serialize SQLite writes (we are
  single-user, so contention is negligible).
- Sync apsw calls are dispatched via ``asyncio.to_thread``. apsw.Connection
  is thread-safe (SQLite is built with SQLITE_THREADSAFE=1).
"""

from __future__ import annotations

import asyncio
import json
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Sequence

import apsw
import numpy as np
import sqlite_vec

from .config import PalaceConfig
from .ids import Tower, make_id


MIGRATION_PATH = Path(__file__).parent / "migrations" / "001_init.sql"


def _pack_float32(arr) -> bytes:
    """Pack a 1-D float32 array as bytes for sqlite-vec."""
    a = np.asarray(arr, dtype="float32").reshape(-1)
    return a.tobytes()


def _unpack_float32(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype="float32")


def _dict_from_row(cursor, row) -> Dict[str, Any]:
    """Build a dict from an apsw Cursor + fetched row using getdescription()."""
    desc = cursor.getdescription()
    return {desc[i][0]: row[i] for i in range(len(desc))}


class PalaceStore:
    """Async-friendly persistence layer for the memory palace."""

    def __init__(self, cfg: PalaceConfig):
        self.cfg = cfg
        self.db_path: Path = cfg.db_path
        self._conn: Optional[apsw.Connection] = None
        self._initialized = False
        self._lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        if self._initialized:
            return
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        await asyncio.to_thread(self._sync_initialize)
        self._initialized = True

    def _sync_initialize(self) -> None:
        self._conn = apsw.Connection(str(self.db_path))
        self._conn.enable_load_extension(True)
        self._conn.load_extension(sqlite_vec.loadable_path())
        # Check whether migration has been applied.
        row = self._conn.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type='table' AND name='north_drawer'"
        ).fetchone()
        if row is None:
            sql = MIGRATION_PATH.read_text().replace("{dim}", str(self.cfg.embedder_dim))
            for _ in self._conn.execute(sql):
                pass

    async def close(self) -> None:
        if self._conn is not None:
            await asyncio.to_thread(self._conn.close)
            self._conn = None

    # ------------------------------------------------------------------
    # connection accessor (sync, for use inside to_thread blocks)
    # ------------------------------------------------------------------

    @contextmanager
    def _sync_conn(self) -> Iterator[apsw.Connection]:
        assert self._conn is not None, "store not initialized"
        yield self._conn

    async def _run_sync(self, fn, *args, **kwargs):
        if not self._initialized:
            await self.initialize()
        return await asyncio.to_thread(fn, *args, **kwargs)

    # Compatibility shim used by tests/other modules expecting async ctx mgr.
    class _AsyncConnWrapper:
        def __init__(self, store):
            self._store = store

        async def __aenter__(self):
            if not self._store._initialized:
                await self._store.initialize()
            return self._store._conn

        async def __aexit__(self, exc_type, exc, tb):
            return False

    def acquire(self) -> "_AsyncConnWrapper":
        return PalaceStore._AsyncConnWrapper(self)

    # ------------------------------------------------------------------
    # 北塔
    # ------------------------------------------------------------------

    async def insert_north_drawer(
        self, *, year: int, floor: int, room: int, drawer: int,
        date: str, raw_messages: List[Dict[str, Any]],
    ) -> str:
        drawer_id = make_id(Tower.NORTH, year, floor, room, drawer)

        def _sync():
            with self._sync_conn() as conn:
                conn.execute(
                    "INSERT INTO north_drawer "
                    "(id, year, floor, room, drawer, date, raw_messages, message_count) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (drawer_id, year, floor, room, drawer, date,
                     json.dumps(raw_messages, ensure_ascii=False),
                     len(raw_messages)),
                )
            return drawer_id

        async with self._lock:
            return await self._run_sync(_sync)

    async def get_north_drawer(self, drawer_id: str) -> Optional[Dict[str, Any]]:
        cols = [
            "id", "year", "floor", "room", "drawer", "date",
            "raw_messages", "message_count", "created_at",
        ]

        def _sync():
            with self._sync_conn() as conn:
                row = conn.execute(
                    f"SELECT {', '.join(cols)} FROM north_drawer WHERE id=?",
                    (drawer_id,),
                ).fetchone()
            if row is None:
                return None
            rec = dict(zip(cols, row))
            rec["raw_messages"] = json.loads(rec["raw_messages"])
            return rec
        return await self._run_sync(_sync)

    # ------------------------------------------------------------------
    # 南塔
    # ------------------------------------------------------------------

    async def insert_south_drawer(
        self, *, year: int, floor: int, room: int, drawer: int,
        date: str, north_ref_ids: List[str],
        room_type: str, room_label: str, drawer_topic: str,
        summary: str, keywords: List[str],
        embedding,
    ) -> str:
        drawer_id = make_id(Tower.SOUTH, year, floor, room, drawer)
        kw_json = json.dumps(keywords, ensure_ascii=False)
        ref_json = json.dumps(north_ref_ids, ensure_ascii=False)
        emb_blob = _pack_float32(embedding)

        def _sync():
            with self._sync_conn() as conn:
                conn.execute(
                    "INSERT INTO south_drawer "
                    "(id, north_ref_ids, year, floor, room, drawer, date, "
                    " room_type, room_label, drawer_topic, summary, keywords) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (drawer_id, ref_json, year, floor, room, drawer, date,
                     room_type, room_label, drawer_topic, summary, kw_json),
                )
                conn.execute(
                    "INSERT INTO south_vec(drawer_id, embedding) VALUES (?, ?)",
                    (drawer_id, emb_blob),
                )
            return drawer_id

        async with self._lock:
            return await self._run_sync(_sync)

    async def get_south_drawer(self, drawer_id: str) -> Optional[Dict[str, Any]]:
        cols = [
            "id", "north_ref_ids", "year", "floor", "room", "drawer", "date",
            "room_type", "room_label", "drawer_topic", "summary", "keywords",
            "merge_count", "created_at",
        ]

        def _sync():
            with self._sync_conn() as conn:
                row = conn.execute(
                    f"SELECT {', '.join(cols)} FROM south_drawer WHERE id=?",
                    (drawer_id,),
                ).fetchone()
            if row is None:
                return None
            rec = dict(zip(cols, row))
            rec["north_ref_ids"] = json.loads(rec["north_ref_ids"])
            rec["keywords"] = json.loads(rec["keywords"]) if rec["keywords"] else []
            return rec
        return await self._run_sync(_sync)

    async def get_south_embedding(self, drawer_id: str) -> Optional[np.ndarray]:
        def _sync():
            with self._sync_conn() as conn:
                row = conn.execute(
                    "SELECT embedding FROM south_vec WHERE drawer_id=?",
                    (drawer_id,),
                ).fetchone()
            return _unpack_float32(row[0]) if row else None
        return await self._run_sync(_sync)

    async def list_room_south_drawers(
        self, *, date: str, room: int,
    ) -> List[Dict[str, Any]]:
        cols = [
            "id", "north_ref_ids", "year", "floor", "room", "drawer", "date",
            "room_type", "room_label", "drawer_topic", "summary", "keywords",
            "merge_count",
        ]

        def _sync():
            with self._sync_conn() as conn:
                rows = conn.execute(
                    f"SELECT {', '.join(cols)} FROM south_drawer "
                    "WHERE date=? AND room=? ORDER BY drawer",
                    (date, room),
                ).fetchall()
            out = []
            for row in rows:
                rec = dict(zip(cols, row))
                rec["north_ref_ids"] = json.loads(rec["north_ref_ids"])
                rec["keywords"] = (
                    json.loads(rec["keywords"]) if rec["keywords"] else []
                )
                out.append(rec)
            return out
        return await self._run_sync(_sync)

    async def vec_knn(
        self, query_emb, *, limit: int = 30,
        year_min: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        emb_blob = _pack_float32(query_emb)

        def _sync():
            with self._sync_conn() as conn:
                if year_min is None:
                    cur = conn.execute(
                        "SELECT drawer_id, distance FROM south_vec "
                        "WHERE embedding MATCH ? AND k = ? "
                        "ORDER BY distance",
                        (emb_blob, limit),
                    )
                else:
                    cur = conn.execute(
                        "SELECT sv.drawer_id, sv.distance FROM south_vec sv "
                        "JOIN south_drawer sd ON sv.drawer_id = sd.id "
                        "WHERE sv.embedding MATCH ? AND sv.k = ? AND sd.year >= ? "
                        "ORDER BY sv.distance",
                        (emb_blob, limit, year_min),
                    )
                return [{"drawer_id": r[0], "distance": r[1]} for r in cur]
        return await self._run_sync(_sync)

    async def fts_search(self, query: str, *, limit: int = 30) -> List[Dict[str, Any]]:
        # FTS5 MATCH will raise on empty/special-char-only queries; guard it.
        q = (query or "").strip()
        if not q:
            return []

        def _sync():
            with self._sync_conn() as conn:
                try:
                    cur = conn.execute(
                        "SELECT drawer_id, bm25(south_fts) AS score "
                        "FROM south_fts WHERE south_fts MATCH ? "
                        "ORDER BY score LIMIT ?",
                        (q, limit),
                    )
                    return [{"drawer_id": r[0], "score": r[1]} for r in cur]
                except apsw.SQLError:
                    # Malformed FTS query — return empty
                    return []
        return await self._run_sync(_sync)

    async def merge_south_drawer(
        self, *, target_id: str, new_north_id: str,
        new_summary: str, new_embedding=None,
    ) -> None:
        emb_blob = _pack_float32(new_embedding) if new_embedding is not None else None

        def _sync():
            with self._sync_conn() as conn:
                row = conn.execute(
                    "SELECT north_ref_ids, merge_count FROM south_drawer WHERE id=?",
                    (target_id,),
                ).fetchone()
                if row is None:
                    raise ValueError(f"no south drawer {target_id}")
                north_refs = json.loads(row[0])
                merge_count = row[1]
                north_refs.append(new_north_id)
                conn.execute(
                    "UPDATE south_drawer SET north_ref_ids=?, summary=?, "
                    "merge_count=? WHERE id=?",
                    (json.dumps(north_refs, ensure_ascii=False), new_summary,
                     merge_count + 1, target_id),
                )
                if emb_blob is not None:
                    conn.execute(
                        "DELETE FROM south_vec WHERE drawer_id=?", (target_id,)
                    )
                    conn.execute(
                        "INSERT INTO south_vec(drawer_id, embedding) VALUES (?, ?)",
                        (target_id, emb_blob),
                    )

        async with self._lock:
            await self._run_sync(_sync)

    # ------------------------------------------------------------------
    # 中庭
    # ------------------------------------------------------------------

    async def insert_atrium_entry(
        self, *, id: str, entry_type: str, content: str,
        source_type: str, status: str,
        evidence_drawer_ids: Optional[List[str]] = None,
        confidence: float = 1.0,
        embedding=None,
    ) -> str:
        now = datetime.now(timezone.utc).isoformat()
        evidence_json = json.dumps(evidence_drawer_ids or [], ensure_ascii=False)
        emb_blob = _pack_float32(embedding) if embedding is not None else None

        def _sync():
            with self._sync_conn() as conn:
                conn.execute(
                    "INSERT INTO atrium_entry "
                    "(id, entry_type, content, source_type, status, "
                    " evidence_drawer_ids, evidence_count, confidence, "
                    " proposed_at, approved_at) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (id, entry_type, content, source_type, status,
                     evidence_json, len(evidence_drawer_ids or []), confidence,
                     now, now if status == "active" else None),
                )
                if emb_blob is not None:
                    conn.execute(
                        "INSERT INTO atrium_vec(entry_id, embedding) VALUES (?,?)",
                        (id, emb_blob),
                    )
                conn.execute(
                    "INSERT INTO atrium_changelog "
                    "(entry_id, old_value, new_value, action, actor) "
                    "VALUES (?, NULL, ?, 'create', ?)",
                    (id,
                     json.dumps({"content": content, "status": status},
                                ensure_ascii=False),
                     "user_cli" if source_type == "explicit" else "auto_proposer"),
                )
            return id

        async with self._lock:
            return await self._run_sync(_sync)

    _ATRIUM_COLS = [
        "id", "entry_type", "content", "source_type", "status",
        "evidence_drawer_ids", "evidence_count", "confidence",
        "has_conflict_with", "proposed_at", "approved_at", "rejected_at",
        "last_confirmed_at", "last_reviewed_at", "created_at", "updated_at",
    ]

    async def get_atrium_entry(self, entry_id: str) -> Optional[Dict[str, Any]]:
        cols = self._ATRIUM_COLS

        def _sync():
            with self._sync_conn() as conn:
                row = conn.execute(
                    f"SELECT {', '.join(cols)} FROM atrium_entry WHERE id=?",
                    (entry_id,),
                ).fetchone()
            if row is None:
                return None
            rec = dict(zip(cols, row))
            rec["evidence_drawer_ids"] = json.loads(
                rec.get("evidence_drawer_ids") or "[]"
            )
            return rec
        return await self._run_sync(_sync)

    async def list_atrium_entries(
        self, *, status: Optional[str] = None,
        entry_type: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        cols = self._ATRIUM_COLS

        def _sync():
            sql = f"SELECT {', '.join(cols)} FROM atrium_entry WHERE 1=1"
            params: List[Any] = []
            if status is not None:
                sql += " AND status=?"
                params.append(status)
            if entry_type is not None:
                sql += " AND entry_type=?"
                params.append(entry_type)
            sql += " ORDER BY proposed_at DESC"
            with self._sync_conn() as conn:
                rows = conn.execute(sql, tuple(params)).fetchall()
            out = []
            for row in rows:
                rec = dict(zip(cols, row))
                rec["evidence_drawer_ids"] = json.loads(
                    rec.get("evidence_drawer_ids") or "[]"
                )
                out.append(rec)
            return out
        return await self._run_sync(_sync)

    async def update_atrium_status(
        self, entry_id: str, new_status: str, *, actor: str = "user_cli",
    ) -> None:
        now = datetime.now(timezone.utc).isoformat()
        action_map = {
            "active": ("approved_at", "approve"),
            "rejected": ("rejected_at", "reject"),
            "archived": ("last_reviewed_at", "archive"),
            "pending": ("updated_at", "update"),
        }
        col, action = action_map.get(new_status, ("updated_at", "update"))

        def _sync():
            with self._sync_conn() as conn:
                old = conn.execute(
                    "SELECT status FROM atrium_entry WHERE id=?", (entry_id,)
                ).fetchone()
                if old is None:
                    raise ValueError(f"no atrium entry {entry_id}")
                conn.execute(
                    f"UPDATE atrium_entry "
                    f"SET status=?, {col}=?, updated_at=? WHERE id=?",
                    (new_status, now, now, entry_id),
                )
                conn.execute(
                    "INSERT INTO atrium_changelog "
                    "(entry_id, old_value, new_value, action, actor) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (entry_id,
                     json.dumps({"status": old[0]}, ensure_ascii=False),
                     json.dumps({"status": new_status}, ensure_ascii=False),
                     action, actor),
                )

        async with self._lock:
            await self._run_sync(_sync)

    async def update_atrium_content(self, entry_id: str, new_content: str) -> None:
        """Edit the content field (CLI edit action)."""
        now = datetime.now(timezone.utc).isoformat()

        def _sync():
            with self._sync_conn() as conn:
                old = conn.execute(
                    "SELECT content FROM atrium_entry WHERE id=?", (entry_id,)
                ).fetchone()
                if old is None:
                    raise ValueError(f"no atrium entry {entry_id}")
                conn.execute(
                    "UPDATE atrium_entry SET content=?, updated_at=? WHERE id=?",
                    (new_content, now, entry_id),
                )
                conn.execute(
                    "INSERT INTO atrium_changelog "
                    "(entry_id, old_value, new_value, action, actor) "
                    "VALUES (?, ?, ?, 'edit', 'user_cli')",
                    (entry_id,
                     json.dumps({"content": old[0]}, ensure_ascii=False),
                     json.dumps({"content": new_content}, ensure_ascii=False)),
                )

        async with self._lock:
            await self._run_sync(_sync)

    async def get_atrium_embedding(self, entry_id: str) -> Optional[np.ndarray]:
        def _sync():
            with self._sync_conn() as conn:
                row = conn.execute(
                    "SELECT embedding FROM atrium_vec WHERE entry_id=?",
                    (entry_id,),
                ).fetchone()
            return _unpack_float32(row[0]) if row else None
        return await self._run_sync(_sync)

    # ------------------------------------------------------------------
    # 辅助表
    # ------------------------------------------------------------------

    async def upsert_day_room(
        self, *, date: str, room: int, room_type: str,
        room_label: str, drawer_count: int,
    ) -> None:
        def _sync():
            with self._sync_conn() as conn:
                conn.execute(
                    "INSERT INTO day_room_map "
                    "(date, room, room_type, room_label, drawer_count) "
                    "VALUES (?,?,?,?,?) "
                    "ON CONFLICT(date, room) DO UPDATE SET "
                    "  room_type=excluded.room_type, "
                    "  room_label=excluded.room_label, "
                    "  drawer_count=excluded.drawer_count",
                    (date, room, room_type, room_label, drawer_count),
                )
        async with self._lock:
            await self._run_sync(_sync)

    async def get_day_room_map(self, date: str) -> Dict[int, Dict[str, Any]]:
        def _sync():
            with self._sync_conn() as conn:
                cur = conn.execute(
                    "SELECT room, room_type, room_label, drawer_count "
                    "FROM day_room_map WHERE date=?", (date,),
                )
                return {
                    r[0]: {"room_type": r[1], "room_label": r[2],
                           "drawer_count": r[3]}
                    for r in cur
                }
        return await self._run_sync(_sync)

    async def log_drawer_merge(
        self, *, target_id: str, merged_from: List[Dict[str, Any]],
        reason: str,
    ) -> None:
        def _sync():
            with self._sync_conn() as conn:
                conn.execute(
                    "INSERT INTO drawer_merge_log(target_id, merged_from, reason) "
                    "VALUES (?,?,?)",
                    (target_id,
                     json.dumps(merged_from, ensure_ascii=False),
                     reason),
                )
        async with self._lock:
            await self._run_sync(_sync)
