"""Hybrid retriever: vec + FTS → RRF merge → rerank."""
from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from .config import PalaceConfig
from .store import PalaceStore


logger = logging.getLogger(__name__)


def _rrf_merge(lists: List[List[Dict[str, Any]]], k: int = 60) -> List[Dict[str, Any]]:
    scores: Dict[str, float] = {}
    repr_: Dict[str, Dict[str, Any]] = {}
    for lst in lists:
        for rank, item in enumerate(lst):
            did = item["drawer_id"]
            scores[did] = scores.get(did, 0.0) + 1.0 / (k + rank + 1)
            repr_.setdefault(did, item)
    return sorted(repr_.values(), key=lambda x: -scores[x["drawer_id"]])


class Retriever:
    def __init__(
        self, *, cfg: PalaceConfig, store: PalaceStore, embedder, reranker,
    ):
        self.cfg = cfg
        self.store = store
        self.embedder = embedder
        self.reranker = reranker

    async def search(
        self, query: str, *,
        now_year: Optional[int] = None, limit: Optional[int] = None,
    ) -> List[Dict[str, Any]]:
        if not query.strip():
            return []
        limit = limit or self.cfg.top_k_south
        year_min = None
        if now_year is not None:
            year_min = now_year - self.cfg.current_year_scope + 1

        try:
            q_emb = self.embedder.encode(query)[0]
            vec_hits = await self.store.vec_knn(
                q_emb, limit=30, year_min=year_min,
            )
        except Exception as exc:
            logger.warning("vector search failed: %s", exc)
            vec_hits = []

        try:
            fts_hits = await self.store.fts_search(query, limit=30)
        except Exception as exc:
            logger.warning("fts search failed: %s", exc)
            fts_hits = []

        merged = _rrf_merge([vec_hits, fts_hits])[:60]
        if not merged:
            return []

        hydrated: List[Dict[str, Any]] = []
        for h in merged:
            row = await self.store.get_south_drawer(h["drawer_id"])
            if row is None:
                continue
            hydrated.append({**h, **row})

        try:
            scores = self.reranker.rerank(
                query, [h["summary"] for h in hydrated],
            )
            for h, s in zip(hydrated, scores):
                h["rerank_score"] = float(s)
            hydrated.sort(key=lambda x: -x.get("rerank_score", 0.0))
        except Exception as exc:
            logger.warning("rerank failed, using RRF order: %s", exc)

        return hydrated[:limit]
