"""lite-eval design §18.16 (llm-steward entry), layer 2: on one loaded tenant, the frozen
original (tests/eval/legacy_recall.py) and the lite-eval runs give the same mean recall@8 and the
same misses, without and with rerank, on SQLite and PostgreSQL. Opt-in like the gate itself: it
needs the real local models."""

from pathlib import Path

import pytest

from tests.conftest import Tenant
from tests.eval import legacy_recall as legacy
from tests.eval.target import METRIC, evaluate, legacy_scores, load, misses, run_scores

pytestmark = pytest.mark.eval


def test_lite_eval_recall_equals_the_original(tenant: Tenant, db: str, tmp_path: Path) -> None:
    keys = load(tenant)  # one loaded tenant: the comparison is of retrieval and scoring
    old, old_misses = legacy._recall(tenant, keys)
    old_rerank, old_rerank_misses = legacy._recall(tenant, keys, rerank=True)
    plain, reranked = evaluate(tenant, keys, db=db, root=tmp_path)
    assert plain.record.summary.metrics[METRIC].value == pytest.approx(old, abs=1e-12)
    assert reranked.record.summary.metrics[METRIC].value == pytest.approx(old_rerank, abs=1e-12)
    assert misses(plain) == old_misses
    assert misses(reranked) == old_rerank_misses
    # case by case, not only on average: offsetting differences cannot pass
    assert run_scores(plain) == legacy_scores(tenant, keys)
    assert run_scores(reranked) == legacy_scores(tenant, keys, rerank=True)
