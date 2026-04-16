"""CLI: python -m mybot memory <subcommand>

Subcommands: init / stats / list / show / review / archive / resurrect / backup
"""
from __future__ import annotations

import argparse
import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from .config import PalaceConfig
from .store import PalaceStore


async def cmd_init(cfg: PalaceConfig) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    print(f"palace.db initialized at {cfg.db_path}")
    await store.close()


async def cmd_stats(cfg: PalaceConfig) -> None:
    store = PalaceStore(cfg)
    await store.initialize()

    def _sync():
        with store._sync_conn() as conn:
            def one(sql):
                return conn.execute(sql).fetchone()[0]
            return {
                "north_drawers": one("SELECT COUNT(*) FROM north_drawer"),
                "south_drawers": one("SELECT COUNT(*) FROM south_drawer"),
                "atrium_total": one("SELECT COUNT(*) FROM atrium_entry"),
                "atrium_active": one(
                    "SELECT COUNT(*) FROM atrium_entry WHERE status='active'",
                ),
                "atrium_pending": one(
                    "SELECT COUNT(*) FROM atrium_entry WHERE status='pending'",
                ),
                "atrium_rejected": one(
                    "SELECT COUNT(*) FROM atrium_entry WHERE status='rejected'",
                ),
                "atrium_archived": one(
                    "SELECT COUNT(*) FROM atrium_entry WHERE status='archived'",
                ),
            }

    stats = await store._run_sync(_sync)
    print(json.dumps(stats, indent=2, ensure_ascii=False))
    await store.close()


async def cmd_list(
    cfg: PalaceConfig, *,
    status: Optional[str], entry_type: Optional[str],
) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    entries = await store.list_atrium_entries(
        status=status, entry_type=entry_type,
    )
    if not entries:
        print("(empty)")
        await store.close()
        return
    for e in entries:
        print(
            f"- [{e['entry_type']:10s}] [{e['status']:8s}] "
            f"{e['id'][:8]}  {e['content'][:80]}"
        )
    await store.close()


async def cmd_show(cfg: PalaceConfig, entry_id: str) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    e = await store.get_atrium_entry(entry_id)
    if e is None:
        print(f"no entry {entry_id}")
    else:
        print(json.dumps(e, indent=2, ensure_ascii=False, default=str))
    await store.close()


async def cmd_review(cfg: PalaceConfig) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    pending = await store.list_atrium_entries(status="pending")
    if not pending:
        print("no pending entries.")
        await store.close()
        return
    for i, e in enumerate(pending, 1):
        print(
            f"\n[{i}/{len(pending)}] pending "
            f"{e['source_type']} {e['entry_type']}"
        )
        print(f"  id:      {e['id']}")
        print(f"  content: {e['content']}")
        evs = e.get("evidence_drawer_ids") or []
        print(
            f"  evidence ({e.get('evidence_count', 0)} drawers): "
            + ", ".join(evs[:5])
        )
        while True:
            choice = input(
                "  [a]pprove / [r]eject / [e]dit / [s]kip: "
            ).strip().lower()
            if choice in {"a", "approve"}:
                await store.update_atrium_status(
                    e["id"], "active", actor="user_cli",
                )
                print("  → active")
                break
            if choice in {"r", "reject"}:
                await store.update_atrium_status(
                    e["id"], "rejected", actor="user_cli",
                )
                print("  → rejected")
                break
            if choice in {"e", "edit"}:
                new = input("    edited content: ").strip()

                def _update():
                    with store._sync_conn() as conn:
                        conn.execute(
                            "UPDATE atrium_entry SET content=? WHERE id=?",
                            (new, e["id"]),
                        )

                await store._run_sync(_update)
                await store.update_atrium_status(
                    e["id"], "active", actor="user_cli",
                )
                print("  → edited + active")
                break
            if choice in {"s", "skip", ""}:
                print("  → skipped")
                break
    await store.close()


async def cmd_archive(cfg: PalaceConfig, entry_id: str) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    await store.update_atrium_status(entry_id, "archived", actor="user_cli")
    print(f"archived {entry_id}")
    await store.close()


async def cmd_resurrect(cfg: PalaceConfig, entry_id: str) -> None:
    store = PalaceStore(cfg)
    await store.initialize()
    await store.update_atrium_status(entry_id, "active", actor="user_cli")
    print(f"resurrected {entry_id}")
    await store.close()


async def cmd_backup(cfg: PalaceConfig) -> None:
    ts = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    dst = cfg.db_path.parent / f"palace.db.bak-{ts}"
    shutil.copy2(cfg.db_path, dst)
    print(f"backup → {dst}")


def _load_cfg() -> PalaceConfig:
    try:
        import yaml  # type: ignore
    except ImportError:
        return PalaceConfig()
    p = Path("config.yaml")
    if not p.exists():
        return PalaceConfig()
    try:
        return PalaceConfig.from_dict(yaml.safe_load(p.read_text()) or {})
    except Exception:
        return PalaceConfig()


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mybot memory")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init")
    sub.add_parser("stats")
    lp = sub.add_parser("list")
    lp.add_argument("--status")
    lp.add_argument("--type", dest="entry_type")
    sp = sub.add_parser("show")
    sp.add_argument("entry_id")
    sub.add_parser("review")
    ap = sub.add_parser("archive")
    ap.add_argument("entry_id")
    rp = sub.add_parser("resurrect")
    rp.add_argument("entry_id")
    sub.add_parser("backup")
    return p


def main(argv: Optional[List[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    cfg = _load_cfg()

    async def _run():
        if args.cmd == "init":
            await cmd_init(cfg)
        elif args.cmd == "stats":
            await cmd_stats(cfg)
        elif args.cmd == "list":
            await cmd_list(
                cfg, status=args.status, entry_type=args.entry_type,
            )
        elif args.cmd == "show":
            await cmd_show(cfg, args.entry_id)
        elif args.cmd == "review":
            await cmd_review(cfg)
        elif args.cmd == "archive":
            await cmd_archive(cfg, args.entry_id)
        elif args.cmd == "resurrect":
            await cmd_resurrect(cfg, args.entry_id)
        elif args.cmd == "backup":
            await cmd_backup(cfg)

    asyncio.run(_run())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
