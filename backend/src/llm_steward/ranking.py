"""Ranking for retrieval (design §6), pure functions.

- Reciprocal rank fusion, shared by the native provider (vector + full text) and the governance
  layer (memory + KB). Ranks, not scores, so engines with incomparable score scales can be merged.
- Business weighting (step 6): trust, importance and time decay.
- MMR (step 7) over a lexical similarity: item vectors live in the providers, and no model runs
  on the request path. Words for Latin text, character bigrams for Chinese (no jieba needed).
- The token budget: a conservative estimate, not a vendor count (it is llm-steward's guard).
"""

import math
import re
from collections.abc import Callable, Hashable, Iterable, Sequence

RRF_K = 60


def rrf_scores[K: Hashable](rankings: Iterable[Sequence[K]]) -> dict[K, float]:
    """Sum of 1 / (RRF_K + rank) over every ranking a key appears in (rank starts at 1)."""
    scores: dict[K, float] = {}
    for ranking in rankings:
        for rank, key in enumerate(ranking, start=1):
            scores[key] = scores.get(key, 0.0) + 1.0 / (RRF_K + rank)
    return scores


IMPORTANCE_BOOST = 0.3


def weigh(
    *,
    trust: float,
    importance: float,
    age_days: float,
    half_life_days: float | None,
    boost: float = IMPORTANCE_BOOST,
) -> float:
    """The weight step 6 multiplies a fused relevance by: w_trust x (1 + boost * importance) x
    0.5^(age / h); no half-life: no decay. A timestamp in the future counts as age 0."""
    decay = 1.0 if half_life_days is None else 0.5 ** (max(age_days, 0.0) / half_life_days)
    return trust * (1 + boost * importance) * decay


_WORDS = re.compile(r"[0-9a-z]+")
_CJK = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]+")


def similarity_terms(text: str) -> set[str]:
    """What two items are compared on: lowercase words, and bigrams of each run of CJK
    characters (a single character stands alone)."""
    lowered = text.lower()
    terms = set(_WORDS.findall(lowered))
    for run in _CJK.findall(lowered):
        terms.update([run] if len(run) == 1 else (run[i : i + 2] for i in range(len(run) - 1)))
    return terms


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    shared = len(a & b)
    return shared / (len(a) + len(b) - shared)


MMR_LAMBDA = 0.7  # design §6 step 7


def mmr[K: Hashable](
    items: Sequence[tuple[K, float, set[str]]],
    k: int,
    lam: float = MMR_LAMBDA,
    accept: Callable[[K], bool] | None = None,
) -> list[K]:
    """Maximal marginal relevance: `items` are (key, relevance, terms), best first. Each step picks
    the item maximising lam * relevance / best - (1 - lam) * its highest similarity to those
    picked (ties: the better ranked). `accept` may refuse the step's choice (e.g. it does not fit
    a budget): a refused item is dropped and counts as neither picked nor seen. Each item's
    highest similarity is kept up to date against the newest pick only: a step costs O(n)."""
    if not items:
        return []
    best = max(score for _, score, _ in items) or 1.0
    left = list(range(len(items)))
    overlap = [0.0] * len(items)
    picked: list[K] = []
    while left and len(picked) < k:
        chosen = max(left, key=lambda i: lam * items[i][1] / best - (1 - lam) * overlap[i])
        left.remove(chosen)
        if accept is not None and not accept(items[chosen][0]):
            continue
        picked.append(items[chosen][0])
        for i in left:
            overlap[i] = max(overlap[i], _jaccard(items[i][2], items[chosen][2]))
    return picked


_CJK_CHAR = re.compile(r"[\u3400-\u9fff]")


def estimate_tokens(text: str) -> int:
    """Rough and cheap, rounded up (a budget must not under-count): a CJK character is about one
    token, other text about four characters. Shared by the episode buffer and retrieval."""
    cjk = len(_CJK_CHAR.findall(text))
    return cjk + math.ceil((len(text) - cjk) / 4)


def fit_budget[T](items: Sequence[T], cost: Callable[[T], int], budget: int) -> list[T]:
    """The items in order, skipping any that would take the total over `budget`."""
    kept, used = [], 0
    for item in items:
        if used + (c := cost(item)) <= budget:
            kept.append(item)
            used += c
    return kept
