"""A cross-encoder stand-in: a logit per text (how many query words it contains, minus 2, or a
fixed table), returned as relevance in [0, 1] like the real adapter (sigmoid); records every call
and can be slowed down or made to fail."""

import asyncio
import math


class FakeReranker:
    model = "fake/reranker"

    def __init__(
        self,
        *,
        scores: dict[str, float] | None = None,
        delay_s: float = 0.0,
        error: Exception | None = None,
    ) -> None:
        self.scores = scores  # fixed logits by text, when given
        self.delay_s = delay_s
        self.error = error
        self.calls: list[tuple[str, list[str]]] = []

    async def score(self, query: str, texts: list[str]) -> list[float]:
        self.calls.append((query, list(texts)))
        if self.delay_s:
            await asyncio.sleep(self.delay_s)
        if self.error is not None:
            raise self.error
        if self.scores is not None:
            logits = [self.scores[t] for t in texts]
        else:
            words = set(query.lower().split())
            logits = [float(sum(w in t.lower() for w in words)) - 2.0 for t in texts]
        return [1 / (1 + math.exp(-x)) for x in logits]
