"""Atrium injection: assemble rules/preferences/facts block for prompt."""
from __future__ import annotations

import logging
from typing import List

import numpy as np

from .config import PalaceConfig
from .store import PalaceStore


logger = logging.getLogger(__name__)


TYPE_LABEL = {"rule": "规则", "preference": "偏好", "fact": "事实"}


class AtriumManager:
    def __init__(
        self, *, cfg: PalaceConfig, store: PalaceStore, embedder,
    ):
        self.cfg = cfg
        self.store = store
        self.embedder = embedder

    async def assemble_block(self, *, query: str, now_year: int) -> str:
        entries = await self.store.list_atrium_entries(status="active")
        rules = [e for e in entries if e["entry_type"] == "rule"]
        prefs = [e for e in entries if e["entry_type"] == "preference"]
        facts = [e for e in entries if e["entry_type"] == "fact"]

        if facts and query.strip():
            try:
                q_emb = self.embedder.encode(query)[0]
                scored: List[tuple[float, dict]] = []
                for f in facts:
                    emb = await self.store.get_atrium_embedding(f["id"])
                    if emb is None:
                        continue
                    scored.append((float(np.dot(q_emb, emb)), f))
                scored.sort(key=lambda x: -x[0])
                facts = [f for _, f in scored[: self.cfg.top_k_fact]]
            except Exception as exc:
                logger.warning("fact vec rank failed: %s", exc)
                facts = facts[: self.cfg.top_k_fact]
        else:
            facts = facts[: self.cfg.top_k_fact]

        if not (rules or prefs or facts):
            return ""

        lines = ["## 🏛️ 用户规则与偏好（中庭·永久）"]
        for e in rules:
            lines.append(f"- [{TYPE_LABEL['rule']}] {e['content']}")
        for e in prefs:
            lines.append(f"- [{TYPE_LABEL['preference']}] {e['content']}")
        for e in facts:
            lines.append(f"- [{TYPE_LABEL['fact']}] {e['content']}")
        return "\n".join(lines)
