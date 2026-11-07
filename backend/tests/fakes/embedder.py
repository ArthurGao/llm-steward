"""Deterministic embedder for tests: hashed bag of jieba tokens, L2-normalised, 384 dims.

Texts sharing words get similar vectors; it captures lexical overlap only, not meaning. Real
semantic behaviour is covered by the opt-in `-m model` test with the actual MiniLM model.
"""

import hashlib
import math

from llm_steward.providers.native.text import tokens

DIM = 384


class FakeEmbedder:
    model = "fake-hashed-bow"
    dim = DIM

    def __init__(self) -> None:
        self.calls = 0

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls += 1
        return [self._one(t) for t in texts]

    @staticmethod
    def _one(text: str) -> list[float]:
        vector = [0.0] * DIM
        for token in tokens(text) or [text]:
            digest = hashlib.blake2b(token.encode(), digest_size=8).digest()
            vector[int.from_bytes(digest[:4], "big") % DIM] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(x * x for x in vector)) or 1.0
        return [x / norm for x in vector]
