"""lite-eval design §18.16 (llm-steward entry), layer 1, in CI: for any reply the API could give,
lite-eval's recall@8 on the target's output equals the frozen original `_recall`: the same mean
and the same misses. Only the HTTP boundary is faked (the tenant's client); no app, no models."""

import random
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.eval import legacy_recall as legacy
from tests.eval.target import METRIC, QUERIES, evaluate, legacy_scores, misses, run_scores

KEYS = sorted({k for _, expected in QUERIES for k in expected} | {"m-other", "e-other"})


class _Reply:
    def __init__(self, items: list[str]) -> None:
        self.status_code = 200
        self.text = ""
        self._items = items

    def json(self) -> dict[str, Any]:
        return {"items": [{"id": i} for i in self._items], "degraded": []}


def _tenant(seed: int) -> tuple[Any, dict[str, str]]:
    """A tenant whose /v1/retrieve answers each query with up to 8 random items: corpus items
    (every other query one of its own keys), an item outside the corpus, and the same item twice
    (as passages of one document)."""
    rng = random.Random(seed)
    ids = {f"id-{k}": k for k in KEYS}
    replies = {}
    for n, (query, expected) in enumerate(QUERIES):
        items = rng.sample(sorted(ids), rng.randint(0, 6))
        if n % 2:  # every other query finds at least one of its keys: partial and full recall
            items.insert(rng.randrange(len(items) + 1), f"id-{expected[0]}")
        if rng.random() < 0.3:
            items.append("id-not-in-the-corpus")
        if items and rng.random() < 0.3:
            items.insert(rng.randrange(len(items)), rng.choice(items))  # repeated
        replies[query] = items[: legacy.TOP_K]

    def post(path: str, json: dict[str, Any], headers: Any) -> _Reply:
        assert path == "/v1/retrieve" and json["top_k"] == legacy.TOP_K
        assert json["include_pinned"] is False
        return _Reply(replies[json["query"]])

    tenant = SimpleNamespace(c=SimpleNamespace(http=SimpleNamespace(post=post)), host={})
    return tenant, ids


@pytest.mark.parametrize("seed", range(8))
def test_lite_eval_recall_equals_the_original_for_any_reply(tmp_path: Path, seed: int) -> None:
    tenant, keys = _tenant(seed)
    old, old_misses = legacy._recall(tenant, keys)
    [plain] = evaluate(tenant, keys, db="fake", root=tmp_path, configs=["plain"])
    assert plain.record.summary.metrics[METRIC].value == pytest.approx(old, abs=1e-12)
    assert misses(plain) == old_misses
    assert run_scores(plain) == legacy_scores(tenant, keys)  # case by case, not only the mean
    assert 0 < old < 1  # some queries fully, some partly or not at all recalled


def test_a_degraded_reply_is_a_failed_case_not_a_lower_recall(tmp_path: Path) -> None:
    """The original test failed outright on `degraded`; on lite-eval it is `invalid_output`,
    counted in the run's target errors (which the gate test asserts are none)."""
    tenant, keys = _tenant(0)
    post = tenant.c.http.post
    slow = QUERIES[0][0]

    def degraded(path: str, json: dict[str, Any], headers: Any) -> Any:
        reply = post(path, json, headers)
        if json["query"] == slow:
            reply.json = lambda: {"items": [], "degraded": ["kb: timeout"]}
        return reply

    tenant.c.http.post = degraded
    [plain] = evaluate(tenant, keys, db="fake", root=tmp_path, configs=["plain"])
    assert plain.record.summary.target_errors == {"invalid_output": 1}
    [score] = [s for s in plain.scores if s.case_id == "q01"]
    assert score.value == 0.0  # in the denominator, as a miss


def test_the_recorded_suite_says_how_to_run_it_again(tmp_path: Path) -> None:
    """Its targets need a running app: `lite-eval run` on it explains instead of failing to
    import (the suite names `built_by_the_test`)."""
    from lite_eval.errors import ConfigError
    from lite_eval.runner import prepare_targets
    from lite_eval.suite import load_suite

    from tests.eval.target import write_dataset, write_suite

    write_dataset(tmp_path)
    suite = load_suite(write_suite(tmp_path, ["plain"]), tmp_path)
    with pytest.raises(ConfigError, match=r"pytest -m eval"):
        prepare_targets(suite)
