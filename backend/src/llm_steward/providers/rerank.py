"""The local cross-encoder for optional rerank (design §6 step 5): fastembed's ONNX
TextCrossEncoder (extra `rerank`, or `native`), checked against fastembed 0.9.1. The model is
named at startup (an unknown one fails there), downloads on first use, then runs offline."""

import math
from typing import Any

from llm_steward.providers.lazy_model import LazyModel


class FastEmbedReranker(LazyModel):
    def __init__(self, model: str, cache_dir: str | None = None) -> None:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        if model not in {m["model"] for m in TextCrossEncoder.list_supported_models()}:
            raise ValueError(f"fastembed has no cross-encoder {model!r}")
        super().__init__(concurrency=1)  # one cross-encoder pass at a time: it uses every core
        self.model = model
        self._cache_dir = cache_dir

    async def score(self, query: str, texts: list[str]) -> list[float]:
        return await self._run(_relevance, query, texts)

    def _load(self) -> Any:
        from fastembed.rerank.cross_encoder import TextCrossEncoder

        return TextCrossEncoder(model_name=self.model, cache_dir=self._cache_dir)


def _relevance(model: Any, query: str, texts: list[str]) -> list[float]:
    """The cross-encoder's logits as relevance in [0, 1] (the Reranker contract)."""
    return [1 / (1 + math.exp(-float(s))) for s in model.rerank(query, texts)]
