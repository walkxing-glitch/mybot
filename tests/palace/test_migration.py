"""Migration 001 smoke test: schema installs cleanly, triggers/tables exist."""
from __future__ import annotations

from pathlib import Path

import apsw
import sqlite_vec


MIGRATION_PATH = Path(__file__).resolve().parents[2] / "mybot/palace/migrations/001_init.sql"


def _open_with_vec(db_path):
    conn = apsw.Connection(str(db_path))
    conn.enable_load_extension(True)
    conn.load_extension(sqlite_vec.loadable_path())
    return conn


def test_migration_001_runs_clean(tmp_path):
    db = tmp_path / "palace.db"
    conn = _open_with_vec(db)
    sql = MIGRATION_PATH.read_text().replace("{dim}", "1024")
    for _ in conn.execute(sql):
        pass

    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type IN ('table','trigger')"
    )}
    for expected in {
        "north_drawer", "south_drawer", "atrium_entry",
        "atrium_changelog", "drawer_merge_log", "day_room_map",
        "atrium_blacklist_guard",
        "south_fts_ai", "south_fts_ad", "south_fts_au",
    }:
        assert expected in tables, f"missing {expected}"


def test_migration_is_idempotent(tmp_path):
    db = tmp_path / "palace.db"
    conn = _open_with_vec(db)
    sql = MIGRATION_PATH.read_text().replace("{dim}", "1024")
    for _ in conn.execute(sql):
        pass
    # Running it a second time must not error.
    conn.execute(sql)
