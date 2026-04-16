"""PalaceConfig dataclass: loaded from config.yaml.

See docs/superpowers/specs/2026-04-16-mybot-memory-palace-design.md §3.3
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List


@dataclass
class AtriumGuards:
    blacklist_patterns: List[str] = field(default_factory=lambda: [
        "不可用", "未能找到", "服务中断", "超时",
        "工具报错", "无法访问", "操作失败", "连接失败",
    ])
    evidence_threshold: int = 3
    evidence_days_span: int = 2
    require_manual_approve: bool = True
    review_cycle_days: int = 30
    stale_archive_days: int = 90


@dataclass
class PalaceConfig:
    enabled: bool = True
    db_path: Path = Path("data/palace.db")
    current_year_scope: int = 3
    embedder: str = "BAAI/bge-m3"
    embedder_dim: int = 1024
    reranker: str = "BAAI/bge-reranker-v2-m3"
    top_k_south: int = 5
    top_k_fact: int = 3
    fixed_rooms: Dict[int, str] = field(default_factory=lambda: {
        1: "消费", 2: "工作", 3: "人际", 4: "健康", 5: "学习",
        6: "技术", 7: "项目", 8: "家庭", 9: "出行", 10: "情绪",
    })
    misc_room: int = 20
    guards: AtriumGuards = field(default_factory=AtriumGuards)

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "PalaceConfig":
        cfg = cls()
        p = d.get("palace") or {}
        if "enabled" in p:
            cfg.enabled = bool(p["enabled"])
        if "db_path" in p:
            cfg.db_path = Path(p["db_path"])
        if "current_year_scope" in p:
            cfg.current_year_scope = int(p["current_year_scope"])
        if "embedder" in p:
            cfg.embedder = str(p["embedder"])
        if "embedder_dim" in p:
            cfg.embedder_dim = int(p["embedder_dim"])
        if "reranker" in p:
            cfg.reranker = str(p["reranker"])
        if "top_k_south" in p:
            cfg.top_k_south = int(p["top_k_south"])
        if "top_k_fact" in p:
            cfg.top_k_fact = int(p["top_k_fact"])

        rooms = d.get("rooms") or {}
        fixed = rooms.get("fixed")
        if isinstance(fixed, dict):
            cfg.fixed_rooms = {int(k): str(v) for k, v in fixed.items()}
        if "misc_room" in rooms:
            cfg.misc_room = int(rooms["misc_room"])

        g = d.get("atrium_guards") or {}
        if g:
            cfg.guards = AtriumGuards(
                blacklist_patterns=list(
                    g.get("blacklist_patterns", cfg.guards.blacklist_patterns)
                ),
                evidence_threshold=int(
                    g.get("evidence_threshold", cfg.guards.evidence_threshold)
                ),
                evidence_days_span=int(
                    g.get("evidence_days_span", cfg.guards.evidence_days_span)
                ),
                require_manual_approve=bool(
                    g.get("require_manual_approve", cfg.guards.require_manual_approve)
                ),
                review_cycle_days=int(
                    g.get("review_cycle_days", cfg.guards.review_cycle_days)
                ),
                stale_archive_days=int(
                    g.get("stale_archive_days", cfg.guards.stale_archive_days)
                ),
            )
        return cfg
