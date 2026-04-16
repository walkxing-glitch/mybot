"""Local reranker wrapper around FlagEmbedding.FlagReranker."""
from __future__ import annotations

import logging
from typing import List

logger = logging.getLogger(__name__)


class Reranker:
    """Wraps BAAI/bge-reranker-v2-m3. Lazy-loads on first rerank()."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-reranker-v2-m3",
        use_fp16: bool = True,
    ):
        self.model_name = model_name
        self.use_fp16 = use_fp16
        self._model = None

    def _lazy_load(self) -> None:
        if self._model is not None:
            return
        from FlagEmbedding import FlagReranker
        logger.info(
            "loading reranker %s (may download ~1GB first time)", self.model_name,
        )
        self._model = FlagReranker(self.model_name, use_fp16=self.use_fp16)

    def rerank(self, query: str, docs: List[str]) -> List[float]:
        """Compute relevance scores (normalized) for (query, doc) pairs."""
        if not docs:
            return []
        self._lazy_load()
        pairs = [[query, d] for d in docs]
        scores = self._model.compute_score(pairs, normalize=True)
        if isinstance(scores, float):
            return [float(scores)]
        return [float(s) for s in scores]
