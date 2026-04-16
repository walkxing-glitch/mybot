"""PalaceConfig loading test."""
from __future__ import annotations

from pathlib import Path

import yaml

from mybot.palace.config import PalaceConfig


def test_defaults():
    cfg = PalaceConfig()
    assert cfg.db_path == Path("data/palace.db")
    assert cfg.embedder == "BAAI/bge-m3"
    assert cfg.embedder_dim == 1024
    assert cfg.fixed_rooms[1] == "消费"
    assert cfg.misc_room == 20
    assert "不可用" in cfg.guards.blacklist_patterns
    assert cfg.guards.require_manual_approve is True


def test_from_repo_config_yaml():
    root = Path(__file__).resolve().parents[2]
    raw = yaml.safe_load((root / "config.yaml").read_text())
    cfg = PalaceConfig.from_dict(raw)
    assert cfg.db_path.name == "palace.db"
    assert cfg.fixed_rooms[1] == "消费"
    assert cfg.fixed_rooms[10] == "情绪"
    assert "不可用" in cfg.guards.blacklist_patterns
    assert "连接失败" in cfg.guards.blacklist_patterns
    assert cfg.embedder == "BAAI/bge-m3"


def test_from_dict_overrides():
    cfg = PalaceConfig.from_dict({
        "palace": {"db_path": "/tmp/test.db", "top_k_south": 10},
        "rooms": {"fixed": {1: "X", 2: "Y"}, "misc_room": 99},
        "atrium_guards": {"evidence_threshold": 5},
    })
    assert cfg.db_path == Path("/tmp/test.db")
    assert cfg.top_k_south == 10
    assert cfg.fixed_rooms == {1: "X", 2: "Y"}
    assert cfg.misc_room == 99
    assert cfg.guards.evidence_threshold == 5
