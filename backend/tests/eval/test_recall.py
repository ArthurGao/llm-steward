"""Opt-in (`uv run pytest -m eval`): recall@8 of joint retrieval on the SYNTHETIC labelled set
(tests/eval/corpus.py), through the API, on the native providers with the real local embedding
(multilingual MiniLM). Design §11 P4 acceptance; the threshold sits just under the measured
baseline (design §11 records it), so a scoring change that loses recall fails here.

Since P8 it runs on lite-eval (tests/eval/target.py): one run without rerank, gated, and one with
rerank, reported only, both on one loaded tenant, as before."""

from pathlib import Path

import pytest
from lite_eval.api import assert_gate

from tests.conftest import Tenant
from tests.eval.target import METRIC, QUERIES, evaluate, load, misses

pytestmark = pytest.mark.eval


def test_recall_at_8(
    tenant: Tenant, db: str, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    plain, reranked = evaluate(tenant, load(tenant), db=db, root=tmp_path)
    with capsys.disabled():
        recall = plain.record.summary.metrics[METRIC].value
        rerank = reranked.record.summary.metrics[METRIC].value
        print(f"\n{METRIC}: {recall:.3f} (rerank: {rerank:.3f}) over {len(QUERIES)} queries ({db})")
        for miss in misses(plain):
            print("  miss:", miss)
        for miss in misses(reranked):
            print("  miss (rerank):", miss)
    # The suite's gate: recall on `plain` only, and no errored case in either run (a degraded
    # reply is a failed case, as the original test failed outright; never a lower recall).
    assert_gate([plain, reranked])
