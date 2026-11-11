"""Optional rerank (design §6 step 5; P4d): a cross-encoder re-scores the best fused items, its
score (sigmoid of the logit) replaces RRF's, and the business weights still apply after it.
A slow or failing reranker leaves the fused order and marks the response degraded."""

import math
from collections.abc import Iterator
from typing import Any

import pytest

from llm_steward.models.enums import KbStatus
from llm_steward.services.governance_service import CreateCollection
from tests.conftest import AppClient, fix_order, kb_entry_op, post_memory, running_app
from tests.fakes.reranker import FakeReranker


def _sigmoid(x: float) -> float:
    return 1 / (1 + math.exp(-x))


@pytest.fixture
def reranker() -> FakeReranker:
    return FakeReranker()


@pytest.fixture
def client(migrated_url: str, reranker: FakeReranker) -> Iterator[AppClient]:
    with running_app(migrated_url, settings={"rerank_timeout_ms": 300}, reranker=reranker) as c:
        yield c


def _logged(c: AppClient, retrieval_id: str) -> tuple[dict[str, Any], dict[str, Any]]:
    """The retrieval log's filters and latencies."""
    from llm_steward.models import RetrievalLog

    log = c.get(RetrievalLog, retrieval_id)
    return dict(log.filters), dict(log.latency_ms)


def _retrieve(c: AppClient, h: dict[str, str], **body: Any) -> Any:
    return c.retrieve(h, **{"query": "refund delay", **body})


def test_the_reranker_reorders_and_its_score_replaces_rrf(
    client: AppClient, reranker: FakeReranker, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, _ = client.new_tenant("acme")
    weak = post_memory(client, h, "the weather is nice")
    strong = post_memory(client, h, "refund delay explained")
    client.drain()
    fix_order(monkeypatch, client.services.providers.memory, [weak, strong])  # RRF: weak first
    plain = _retrieve(client, h, sources=["memory"])
    assert [i["id"] for i in plain["items"]] == [weak, strong] and reranker.calls == []
    body = _retrieve(client, h, sources=["memory"], rerank=True)
    assert [i["id"] for i in body["items"]] == [strong, weak]
    assert body["degraded"] == []
    [(query, texts)] = reranker.calls
    assert query == "refund delay" and len(texts) == 2
    # logit 0 (both words) -> 0.5, x the memory weight 0.9 x 1.15
    assert body["items"][0]["score"] == pytest.approx(_sigmoid(0.0) * 0.9 * 1.15)
    filters, latency = _logged(client, body["retrieval_id"])
    assert filters["reranked_by"] == "fake/reranker" and "rerank" in latency


def test_trust_still_weighs_after_rerank(
    client: AppClient, reranker: FakeReranker, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, ns = client.new_tenant("acme")
    collection = client.apply(CreateCollection(ns, "faq"))
    active = client.apply(kb_entry_op(ns, collection.id, KbStatus.ACTIVE, title="A"))
    verified = client.apply(kb_entry_op(ns, collection.id, KbStatus.VERIFIED, title="V"))
    client.drain()
    fix_order(monkeypatch, client.services.providers.kb, [str(active.id), str(verified.id)])
    texts = {"A Refunds take 3 to 5 business days", "V Refunds take 3 to 5 business days"}
    reranker.scores = dict.fromkeys(texts, 1.0)  # the same logit for both: the weights decide
    body = _retrieve(client, h, sources=["kb"], rerank=True)
    assert [i["id"] for i in body["items"]] == [str(verified.id), str(active.id)]


@pytest.mark.parametrize("failure", ["slow", "error"])
def test_a_slow_or_failing_reranker_keeps_the_fused_order_and_says_so(
    client: AppClient, reranker: FakeReranker, monkeypatch: pytest.MonkeyPatch, failure: str
) -> None:
    h, _ = client.new_tenant("acme")
    weak = post_memory(client, h, "the weather is nice")
    strong = post_memory(client, h, "refund delay explained")
    client.drain()
    fix_order(monkeypatch, client.services.providers.memory, [weak, strong])
    if failure == "slow":
        reranker.delay_s = 2.0  # the rerank budget is 300 ms here
    else:
        reranker.error = RuntimeError("onnx runtime failed")
    body = _retrieve(client, h, sources=["memory"], rerank=True)
    assert [i["id"] for i in body["items"]] == [weak, strong]
    assert body["degraded"] == ["rerank"]
    filters, latency = _logged(client, body["retrieval_id"])
    assert (filters["rerank"], filters["reranked_by"]) == (True, None) and "rerank" in latency


def test_only_the_best_fused_items_are_reranked(
    client: AppClient, reranker: FakeReranker, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, _ = client.new_tenant("acme")
    ids = [post_memory(client, h, f"refund note {i}") for i in range(40)]
    client.drain()
    fix_order(monkeypatch, client.services.providers.memory, ids)
    _retrieve(client, h, sources=["memory"], rerank=True, top_k=50)
    [(_, texts)] = reranker.calls
    assert len(texts) == 30  # design §6 step 5: the top 30


def test_a_late_fused_item_the_reranker_favours_reaches_the_top(
    client: AppClient, reranker: FakeReranker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Reranked items are re-sorted before MMR looks at its pool (top_k x 3 = 6 here): the
    reranker's favourite at fused rank 28 must not be cut off."""
    h, _ = client.new_tenant("acme")
    ids = [post_memory(client, h, f"note number {i}") for i in range(30)]
    client.drain()
    fix_order(monkeypatch, client.services.providers.memory, ids)
    # the reranked text is "<title> <content>"; a memory has no title
    reranker.scores = {f" note number {i}": (5.0 if i == 27 else -5.0) for i in range(30)}
    body = _retrieve(client, h, sources=["memory"], rerank=True, top_k=2)
    assert body["items"][0]["id"] == ids[27]


def test_a_namespace_can_rerank_by_default_and_a_request_can_say_otherwise(
    client: AppClient, reranker: FakeReranker, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, ns = client.new_tenant("acme")
    weak = post_memory(client, h, "the weather is nice")
    strong = post_memory(client, h, "refund delay explained")
    client.drain()
    fix_order(monkeypatch, client.services.providers.memory, [weak, strong])
    client.set_config(ns, {"retrieval": {"rerank": True}})
    body = _retrieve(client, h, sources=["memory"])  # no `rerank` in the request
    assert [i["id"] for i in body["items"]] == [strong, weak] and len(reranker.calls) == 1
    body = _retrieve(client, h, sources=["memory"], rerank=False)
    assert [i["id"] for i in body["items"]] == [weak, strong] and len(reranker.calls) == 1


def test_a_namespace_default_without_a_reranker_degrades_instead_of_failing(
    migrated_url: str,
) -> None:
    with running_app(migrated_url) as c:  # no reranker configured
        h, ns = c.new_tenant("acme")
        post_memory(c, h, "refund delay explained")
        c.drain()
        c.set_config(ns, {"retrieval": {"rerank": True}})
        r = c.http.post("/v1/retrieve", json={"query": "refund"}, headers=h)
        assert r.status_code == 200 and r.json()["degraded"] == ["rerank"]
        explicit = c.http.post("/v1/retrieve", json={"query": "refund", "rerank": True}, headers=h)
        assert explicit.status_code == 422  # asked for by the caller: refused


def test_the_cross_encoder_reads_a_bounded_text_per_item(
    client: AppClient, reranker: FakeReranker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A cross-encoder reads ~512 tokens anyway; tokenising a 20 000-character memory for it
    only costs time."""
    h, _ = client.new_tenant("acme")
    huge = post_memory(client, h, "refund " + "x" * 19_990)
    client.drain()
    fix_order(monkeypatch, client.services.providers.memory, [huge])
    _retrieve(client, h, sources=["memory"], rerank=True, token_budget=100_000)
    [(_, texts)] = reranker.calls
    assert texts[0].startswith(" refund") and len(texts[0]) <= 2000


def test_a_relevance_floor_drops_what_the_cross_encoder_finds_unrelated(
    client: AppClient, reranker: FakeReranker, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`retrieval.min_relevance` (off by default) applies to the cross-encoder's relevance, the
    one calibrated score: below it an item is dropped; nothing left is a gap. Without a rerank
    (off, failed, out of time) there is nothing calibrated to compare, so nothing is dropped."""
    from llm_steward.models import RetrievalLog

    h, ns = client.new_tenant("acme")
    weak = post_memory(client, h, "the weather is nice")  # relevance sigmoid(-2) = 0.12
    strong = post_memory(client, h, "refund delay explained")  # sigmoid(0) = 0.5
    client.drain()
    fix_order(monkeypatch, client.services.providers.memory, [weak, strong])
    client.set_config(ns, {"retrieval": {"rerank": True, "min_relevance": 0.3}})

    body = _retrieve(client, h, sources=["memory"])
    assert [i["id"] for i in body["items"]] == [strong]
    nothing = _retrieve(client, h, sources=["memory"], query="parcel tracking")  # both 0.12
    assert nothing["items"] == []
    assert client.get(RetrievalLog, nothing["retrieval_id"]).hit_count == 0  # a gap

    unranked = _retrieve(client, h, sources=["memory"], rerank=False)
    assert [i["id"] for i in unranked["items"]] == [weak, strong]
    reranker.error = RuntimeError("model down")
    failed = _retrieve(client, h, sources=["memory"])
    assert failed["degraded"] == ["rerank"] and len(failed["items"]) == 2
