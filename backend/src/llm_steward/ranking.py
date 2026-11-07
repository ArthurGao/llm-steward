"""Reciprocal rank fusion, shared by the native provider (vector + full text) and the governance
layer (memory + KB). Ranks, not scores, so engines with incomparable score scales can be merged."""

from collections.abc import Hashable, Iterable, Sequence

RRF_K = 60


def rrf_scores[K: Hashable](rankings: Iterable[Sequence[K]]) -> dict[K, float]:
    """Sum of 1 / (RRF_K + rank) over every ranking a key appears in (rank starts at 1)."""
    scores: dict[K, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
    return scores
