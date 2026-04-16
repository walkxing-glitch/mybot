"""Palace CLI command smoke tests."""
from __future__ import annotations

import json
import uuid

from mybot.palace.cli import (
    cmd_archive, cmd_backup, cmd_init, cmd_list, cmd_resurrect,
    cmd_show, cmd_stats,
)
from mybot.palace.config import PalaceConfig
from mybot.palace.store import PalaceStore


async def test_cli_init_creates_db(tmp_path, capsys):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    await cmd_init(cfg)
    assert cfg.db_path.exists()
    out = capsys.readouterr().out
    assert "initialized" in out


async def test_cli_stats_empty(tmp_path, capsys):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    await cmd_init(cfg)
    capsys.readouterr()
    await cmd_stats(cfg)
    out = capsys.readouterr().out
    stats = json.loads(out)
    assert stats["north_drawers"] == 0
    assert stats["south_drawers"] == 0
    assert stats["atrium_total"] == 0


async def test_cli_list_empty(tmp_path, capsys):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    await cmd_init(cfg)
    capsys.readouterr()
    await cmd_list(cfg, status=None, entry_type=None)
    assert "(empty)" in capsys.readouterr().out


async def test_cli_show_missing(tmp_path, capsys):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    await cmd_init(cfg)
    capsys.readouterr()
    await cmd_show(cfg, "nonexistent-id")
    assert "no entry" in capsys.readouterr().out


async def test_cli_archive_and_resurrect_round_trip(tmp_path, capsys):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    store = PalaceStore(cfg)
    await store.initialize()
    eid = str(uuid.uuid4())
    await store.insert_atrium_entry(
        id=eid, entry_type="rule", content="test rule",
        source_type="explicit", status="active",
    )
    await store.close()

    await cmd_archive(cfg, eid)
    store = PalaceStore(cfg)
    await store.initialize()
    e = await store.get_atrium_entry(eid)
    assert e["status"] == "archived"
    await store.close()

    await cmd_resurrect(cfg, eid)
    store = PalaceStore(cfg)
    await store.initialize()
    e = await store.get_atrium_entry(eid)
    assert e["status"] == "active"
    await store.close()


async def test_cli_list_with_entries(tmp_path, capsys):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    store = PalaceStore(cfg)
    await store.initialize()
    await store.insert_atrium_entry(
        id=str(uuid.uuid4()), entry_type="rule",
        content="别启动 myontology/backend",
        source_type="explicit", status="active",
    )
    await store.close()

    capsys.readouterr()
    await cmd_list(cfg, status="active", entry_type="rule")
    out = capsys.readouterr().out
    assert "myontology/backend" in out


async def test_cli_backup(tmp_path, capsys):
    cfg = PalaceConfig(db_path=tmp_path / "palace.db")
    await cmd_init(cfg)
    capsys.readouterr()
    await cmd_backup(cfg)
    out = capsys.readouterr().out
    assert "backup" in out
    backups = list(tmp_path.glob("palace.db.bak-*"))
    assert len(backups) == 1
