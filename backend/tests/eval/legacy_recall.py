"""`tests/eval/test_recall.py` as it was before P8 (commit 52a8d07): `_recall`, the scoring,
frozen for the equivalence tests (tests/eval/test_recall_equivalence.py and
tests/unit/test_eval_recall_scoring.py); the corpus loader moved unchanged to
`tests/eval/target.py`. Not used anywhere else; do not change it."""

from typing import Any

from tests.conftest import Tenant
from tests.eval.corpus import QUERIES

TOP_K = 8
USER = "u1"


def _recall(t: Tenant, keys: dict[str, str], **body: Any) -> tuple[float, list[str]]:
    """Mean recall@TOP_K over the queries, and the queries that missed something."""
    scores, misses = [], []
    for query, expected in QUERIES:
        payload = {"query": query, "scope": {"user_id": USER}, "top_k": TOP_K, **body}
        r = t.c.http.post("/v1/retrieve", json={**payload, "include_pinned": False}, headers=t.host)
        assert r.status_code == 200, r.text
        assert r.json()["degraded"] == [], r.text
        got = {keys.get(i["id"]) for i in r.json()["items"]}
        found = len(set(expected) & got) / len(expected)
        scores.append(found)
        if found < 1:
            misses.append(f"{query!r}: wanted {expected}, got {sorted(k for k in got if k)}")
    return sum(scores) / len(scores), misses
