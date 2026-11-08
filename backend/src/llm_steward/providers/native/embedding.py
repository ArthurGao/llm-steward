"""Embedders for the native provider. Real runs use fastembed (local ONNX); tests use a fake."""

import asyncio
from typing import Any, Protocol


class Embedder(Protocol):
    @property
    def model(self) -> str: ...

    @property
    def dim(self) -> int: ...

    async def embed(self, texts: list[str]) -> list[list[float]]: ...


def model_dim(model: str) -> int:
    """Vector size of a fastembed model, from its metadata (no download)."""
    from fastembed import TextEmbedding

    known = {m["model"]: m for m in TextEmbedding.list_supported_models()}
    if model not in known:
        raise ValueError(f"fastembed does not support embedding model {model!r}")
    return int(known[model]["dim"])


class FastEmbedEmbedder:
    """Local multilingual embeddings. The model downloads once on first use, then runs offline.

    Loading is deferred to the first `embed` call so startup and /health never block on it.
    """

    def __init__(self, model: str, cache_dir: str | None = None) -> None:
        self._model_name = model
        self._dim = model_dim(model)
        self._cache_dir = cache_dir
        self._impl: Any = None
        self._lock = asyncio.Lock()

    @property
    def model(self) -> str:
        return self._model_name

    @property
    def dim(self) -> int:
        return self._dim

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if self._impl is None:
            async with self._lock:
                if self._impl is None:
                    self._impl = await asyncio.to_thread(self._load)
        return await asyncio.to_thread(self._embed_sync, texts)

    def _load(self) -> Any:
        from fastembed import TextEmbedding

        return TextEmbedding(model_name=self._model_name, cache_dir=self._cache_dir)

    def _embed_sync(self, texts: list[str]) -> list[list[float]]:
        return [vector.tolist() for vector in self._impl.embed(texts)]
