"""Doubao (VolcEngine) API-based embedder — no local torch required."""
from __future__ import annotations

import logging
import os
from typing import Sequence, Union

import httpx
import numpy as np

logger = logging.getLogger(__name__)

API_URL = "https://ark.cn-beijing.volces.com/api/v3/embeddings/multimodal"
DEFAULT_MODEL = "doubao-embedding-vision-251215"
DEFAULT_KEY = ""


class DoubaoEmbedder:
    """Remote embedder via doubao multimodal embedding API (2048-dim)."""

    def __init__(self, *, dim: int = 2048, model: str = DEFAULT_MODEL):
        self.dim = dim
        self.model_name = f"doubao:{model}"
        self._model_id = model
        self._api_key = os.environ.get("DOUBAO_API_KEY", DEFAULT_KEY)
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(timeout=30)
        return self._client

    def encode(self, texts: Union[str, Sequence[str]]) -> np.ndarray:
        """Returns shape (N, dim), L2-normalized float32."""
        if isinstance(texts, str):
            texts = [texts]
        texts = list(texts)

        vecs = []
        client = self._get_client()
        for text in texts:
            resp = client.post(
                API_URL,
                headers={
                    "Authorization": f"Bearer {self._api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self._model_id,
                    "input": [{"type": "text", "text": text}],
                },
            )
            resp.raise_for_status()
            data = resp.json()
            emb = data["data"]["embedding"]
            vecs.append(emb)

        arr = np.array(vecs, dtype="float32")
        norms = np.linalg.norm(arr, axis=1, keepdims=True) + 1e-8
        return arr / norms
