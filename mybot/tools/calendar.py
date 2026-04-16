"""Calendar / reminders tool — SQLite-backed local scheduling."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import aiosqlite

from mybot.tools.base import BaseTool, ToolResult

DEFAULT_DB_PATH = "data/mybot.db"

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS reminders (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    description TEXT,
    due_at TIMESTAMP,
    repeat TEXT,
    status TEXT DEFAULT 'active',
    created_at TIMESTAMP
)
"""

_ALLOWED_OPS = ("create", "list", "complete", "delete")
_LIST_SCOPES = ("upcoming", "overdue", "all")


def _parse_due(raw: str) -> datetime | None:
    """Parse an ISO-ish timestamp to a tz-aware UTC datetime."""
    if not raw:
        return None
    raw = raw.strip()
    # Accept trailing 'Z' as UTC.
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        # Try a couple of common fallbacks.
        for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(raw, fmt)
                break
            except ValueError:
                continue
        else:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt


class CalendarTool(BaseTool):
    """Create / list / complete / delete reminders in a local SQLite table."""

    name = "calendar"
    description = (
        "Manage local reminders and scheduled items. Operations: "
        "'create' (title + due_at + optional description/repeat), "
        "'list' (scope: upcoming|overdue|all), "
        "'complete' (mark done), 'delete' (remove)."
    )
    parameters = {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": list(_ALLOWED_OPS),
                "description": "Which calendar operation to perform.",
            },
            "title": {"type": "string", "description": "Reminder title (for create)."},
            "description": {"type": "string", "description": "Optional description (for create)."},
            "due_at": {
                "type": "string",
                "description": "Due time in ISO-8601, e.g. '2026-04-17T09:00:00'. UTC assumed if no tz.",
            },
            "repeat": {
                "type": "string",
                "description": "Optional repeat tag, e.g. 'daily', 'weekly', 'monthly'.",
            },
            "scope": {
                "type": "string",
                "enum": list(_LIST_SCOPES),
                "description": "For 'list': upcoming (default) / overdue / all.",
            },
            "reminder_id": {
                "type": "string",
                "description": "Reminder ID (for complete / delete).",
            },
        },
        "required": ["operation"],
    }

    def __init__(self, db_path: str = DEFAULT_DB_PATH) -> None:
        self.db_path = db_path
        # Make sure parent dir exists so first connect doesn't fail.
        parent = Path(self.db_path).expanduser().parent
        if str(parent) and not parent.exists():
            try:
                parent.mkdir(parents=True, exist_ok=True)
            except Exception:  # noqa: BLE001
                pass

    async def _ensure_schema(self, db: aiosqlite.Connection) -> None:
        await db.execute(_CREATE_SQL)
        await db.commit()

    # -------------------------------------------------------------- operations

    async def execute(self, **params) -> ToolResult:
        op = params.get("operation")
        if op not in _ALLOWED_OPS:
            return ToolResult(
                success=False,
                output="",
                error=f"Unknown operation {op!r}. Allowed: {', '.join(_ALLOWED_OPS)}.",
            )

        try:
            async with aiosqlite.connect(self.db_path) as db:
                await self._ensure_schema(db)
                db.row_factory = aiosqlite.Row

                if op == "create":
                    return await self._create(db, params)
                if op == "list":
                    return await self._list(db, params)
                if op == "complete":
                    return await self._complete(db, params)
                if op == "delete":
                    return await self._delete(db, params)
        except Exception as exc:  # noqa: BLE001
            return ToolResult(success=False, output="", error=f"Calendar error: {exc}")

        return ToolResult(success=False, output="", error="Unreachable branch.")

    # ---- create -------------------------------------------------------------

    async def _create(self, db: aiosqlite.Connection, params: dict) -> ToolResult:
        title = (params.get("title") or "").strip()
        if not title:
            return ToolResult(success=False, output="", error="'title' is required for create.")
        description = params.get("description") or ""
        repeat = params.get("repeat") or ""
        due_raw = params.get("due_at") or ""
        due = _parse_due(due_raw) if due_raw else None
        if due_raw and due is None:
            return ToolResult(
                success=False,
                output="",
                error=f"Could not parse 'due_at': {due_raw!r}. Use ISO-8601.",
            )

        rid = str(uuid.uuid4())
        now = datetime.now(tz=timezone.utc)
        await db.execute(
            "INSERT INTO reminders (id, title, description, due_at, repeat, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, 'active', ?)",
            (
                rid,
                title,
                description,
                due.isoformat() if due else None,
                repeat,
                now.isoformat(),
            ),
        )
        await db.commit()
        due_label = due.isoformat() if due else "(no due date)"
        return ToolResult(
            success=True,
            output=f"Created reminder {rid}: {title} — due {due_label}",
        )

    # ---- list ---------------------------------------------------------------

    async def _list(self, db: aiosqlite.Connection, params: dict) -> ToolResult:
        scope = (params.get("scope") or "upcoming").lower()
        if scope not in _LIST_SCOPES:
            return ToolResult(
                success=False,
                output="",
                error=f"Unknown scope {scope!r}. Allowed: {', '.join(_LIST_SCOPES)}.",
            )
        now_iso = datetime.now(tz=timezone.utc).isoformat()

        if scope == "upcoming":
            sql = (
                "SELECT * FROM reminders "
                "WHERE status = 'active' AND (due_at IS NULL OR due_at >= ?) "
                "ORDER BY due_at IS NULL, due_at ASC"
            )
            args: Iterable = (now_iso,)
        elif scope == "overdue":
            sql = (
                "SELECT * FROM reminders "
                "WHERE status = 'active' AND due_at IS NOT NULL AND due_at < ? "
                "ORDER BY due_at ASC"
            )
            args = (now_iso,)
        else:  # all
            sql = "SELECT * FROM reminders ORDER BY created_at DESC"
            args = ()

        async with db.execute(sql, args) as cur:
            rows = await cur.fetchall()
        if not rows:
            return ToolResult(success=True, output=f"No reminders (scope={scope}).")

        lines = [f"{scope.capitalize()} reminders ({len(rows)}):"]
        for r in rows:
            due = r["due_at"] or "(no due)"
            rep = f" [repeat={r['repeat']}]" if r["repeat"] else ""
            status = r["status"]
            desc = f" — {r['description']}" if r["description"] else ""
            lines.append(f"  [{r['id'][:8]}] {r['title']} (due {due}, {status}){rep}{desc}")
        return ToolResult(success=True, output="\n".join(lines))

    # ---- complete / delete --------------------------------------------------

    async def _find_id(self, db: aiosqlite.Connection, rid_prefix: str) -> str | None:
        """Resolve short-prefix ID to a full ID; return None if ambiguous or missing."""
        if len(rid_prefix) >= 32:
            return rid_prefix
        async with db.execute(
            "SELECT id FROM reminders WHERE id LIKE ?",
            (rid_prefix + "%",),
        ) as cur:
            rows = await cur.fetchall()
        if len(rows) == 1:
            return rows[0]["id"]
        return None

    async def _complete(self, db: aiosqlite.Connection, params: dict) -> ToolResult:
        rid = (params.get("reminder_id") or "").strip()
        if not rid:
            return ToolResult(success=False, output="", error="'reminder_id' is required for complete.")
        full_id = await self._find_id(db, rid)
        if full_id is None:
            return ToolResult(success=False, output="", error=f"No single reminder matches id {rid!r}.")
        await db.execute("UPDATE reminders SET status='completed' WHERE id=?", (full_id,))
        await db.commit()
        return ToolResult(success=True, output=f"Marked reminder {full_id} as completed.")

    async def _delete(self, db: aiosqlite.Connection, params: dict) -> ToolResult:
        rid = (params.get("reminder_id") or "").strip()
        if not rid:
            return ToolResult(success=False, output="", error="'reminder_id' is required for delete.")
        full_id = await self._find_id(db, rid)
        if full_id is None:
            return ToolResult(success=False, output="", error=f"No single reminder matches id {rid!r}.")
        await db.execute("DELETE FROM reminders WHERE id=?", (full_id,))
        await db.commit()
        return ToolResult(success=True, output=f"Deleted reminder {full_id}.")


tools = [CalendarTool()]
