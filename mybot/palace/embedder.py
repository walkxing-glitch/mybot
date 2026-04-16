"""Local embedder wrapper around FlagEmbedding.BGEM3FlagModel."""
from __future__ import annotations

import logging
from typing import Sequence, Union

import numpy as np


logger = logging.getLogger(__name__)


class Embedder:
    """Wraps BAAI/bge-m3. Lazy-loads on first encode()."""

    def __init__(
        self,
        model_name: str = "BAAI/bge-m3",
        dim: int = 1024,
        use_fp16: bool = True,
    ):
        self.model_name = model_name
        self.dim = dim
        self.use_fp16 = use_fp16
        self._model = None

    def _lazy_load(self) -> None:
        if self._model is not None:
            return
        from FlagEmbedding import BGEM3FlagModel
        logger.info(
            "loading embedder %s (may download ~2GB first time)", self.model_name,
        )
        self._model = BGEM3FlagModel(self.model_name, use_fp16=self.use_fp16)

    def encode(self, texts: Union[str, Sequence[str]]) -> np.ndarray:
        """Returns shape (N, dim), L2-normalized float32."""
        self._lazy_load()
        if isinstance(texts, str):
            texts = [texts]
        out = self._model.encode(
            list(texts), max_length=512, return_dense=True,
        )["dense_vecs"]
        arr = np.asarray(out, dtype="float32")
        # bge-m3 already returns normalized, but be defensive:
        norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8
        return arr / norms
