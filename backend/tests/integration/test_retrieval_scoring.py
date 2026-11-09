"""Joint retrieval scoring (design §6 steps 3, 6-9; P4b) through the API, on both dialects.

Provider order is fixed by patching `search` where a test needs a known rank: the weighting is
what is under test, not the engines' ranking."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import update

from llm_steward.models import Memory
from llm_steward.models.base import utcnow
from llm_steward.models.enums import KbStatus, RiskLevel
from llm_steward.ranking import estimate_tokens
from llm_steward.services.governance_service import CreateCollection
from llm_steward.storage.db import session_factory
from tests.conftest import AppClient, fix_order, kb_entry_op, post_memory


def _retrieve(c: AppClient, h: dict[str, str], query: str, **body: Any) -> Any:
    r = c.http.post("/v1/retrieve", json={"query": query, **body}, headers=h)
    return r


def _ids(r: Any) -> list[str]:
    assert r.status_code == 200, r.text
    return [i["id"] for i in r.json()["items"]]


def _age(c: AppClient, memory_id: str, days: int) -> None:
    async def write() -> None:
        async with session_factory(c.services.engine)() as s, s.begin():
            when = utcnow() - timedelta(days=days)
            await s.execute(
                update(Memory)
                .where(Memory.id == uuid.UUID(memory_id))
                .values(valid_from=when, created_at=when)
            )

    c.call(write)


def test_verified_knowledge_outranks_active_at_the_next_rank(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, ns = app_client.new_tenant("acme")
    collection = app_client.apply(CreateCollection(ns, "faq"))
    active = app_client.apply(kb_entry_op(ns, collection.id, KbStatus.ACTIVE, title="A"))
    verified = app_client.apply(kb_entry_op(ns, collection.id, KbStatus.VERIFIED, title="V"))
    app_client.drain()
    # active at rank 1, verified at rank 2: 1/61 * 0.9 < 1/62 * 1.0
    fix_order(monkeypatch, app_client.services.providers.kb, [str(active.id), str(verified.id)])
    r = _retrieve(app_client, h, "refunds", sources=["kb"])
    assert _ids(r) == [str(verified.id), str(active.id)]
    scores = [i["score"] for i in r.json()["items"]]
    assert scores == pytest.approx([1 / 62 * 1.0 * 1.15, 1 / 61 * 0.9 * 1.15])


def test_an_old_episodic_memory_sinks_and_semantic_ones_do_not_decay(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, _ = app_client.new_tenant("acme")
    old_episode = post_memory(app_client, h, "asked about a refund", memory_type="episodic")
    old_fact = post_memory(app_client, h, "prefers email", memory_type="semantic")
    fresh = post_memory(app_client, h, "asked about shipping", memory_type="episodic")
    app_client.drain()
    _age(app_client, old_episode, 60)  # two half-lives: a quarter of its weight
    _age(app_client, old_fact, 365)
    provider = app_client.services.providers.memory
    fix_order(monkeypatch, provider, [old_episode, old_fact, fresh])
    r = _retrieve(app_client, h, "refund", sources=["memory"])
    assert _ids(r) == [old_fact, fresh, old_episode]
    by_id = {i["id"]: i["score"] for i in r.json()["items"]}
    assert by_id[old_episode] == pytest.approx(1 / 61 * 0.9 * 1.15 * 0.25, rel=1e-3)
    assert by_id[old_fact] == pytest.approx(1 / 62 * 0.9 * 1.15)


def test_low_confidence_and_importance_change_the_weight(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, _ = app_client.new_tenant("acme")
    unsure = post_memory(app_client, h, "maybe lives in Berlin", confidence=0.3, importance=0.5)
    key = post_memory(app_client, h, "allergic to nuts", importance=1.0)
    app_client.drain()
    fix_order(monkeypatch, app_client.services.providers.memory, [unsure, key])
    by_id = {
        i["id"]: i["score"]
        for i in _retrieve(app_client, h, "x", sources=["memory"]).json()["items"]
    }
    assert by_id[unsure] == pytest.approx(1 / 61 * 0.9 * 0.8 * 1.15)
    assert by_id[key] == pytest.approx(1 / 62 * 0.9 * 1.3)


def test_namespace_config_overrides_weights_half_lives_and_top_k(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, ns = app_client.new_tenant("acme")
    ids = [post_memory(app_client, h, f"note {i}", memory_type="order_event") for i in range(4)]
    app_client.drain()
    _age(app_client, ids[0], 10)
    app_client.set_config(
        ns,
        {
            "retrieval": {"top_k": 2, "importance_boost": 0.0, "trust_weights": {"active": 0.5}},
            "memory": {"types": [{"name": "order_event", "half_life_days": 10}]},
        },
    )
    fix_order(monkeypatch, app_client.services.providers.memory, ids)
    items = _retrieve(app_client, h, "note", sources=["memory"]).json()["items"]
    assert [i["id"] for i in items] == [ids[1], ids[2]]  # top_k 2; ids[0] halved by decay
    assert items[0]["score"] == pytest.approx(1 / 62 * 0.5)


def test_filters_narrow_after_the_safety_net(app_client: AppClient) -> None:
    h, ns = app_client.new_tenant("acme")
    refund = post_memory(
        app_client, h, "refund for order 1", tags=["refund", "vip"], metadata={"region": "cn"}
    )
    post_memory(app_client, h, "refund for order 2", tags=["refund"], metadata={"region": "eu"})
    episode = post_memory(app_client, h, "refund call yesterday", memory_type="episodic")
    collection = app_client.apply(CreateCollection(ns, "faq", default_risk=RiskLevel.LOW))
    app_client.apply(kb_entry_op(ns, collection.id, KbStatus.ACTIVE))
    r = app_client.http.post(
        "/v1/kb/documents",
        data={"collection_id": str(collection.id)},
        files={"file": ("refunds.md", b"Refund policy: refunds take seven days.", "text/markdown")},
        headers=h,
    )
    assert r.status_code == 202, r.text
    app_client.drain()
    everything = _ids(_retrieve(app_client, h, "refund policy", sources=["kb"]))
    assert r.json()["document"]["id"] in everything  # the document is found without filters

    tagged = _retrieve(app_client, h, "refund", filters={"tags": ["refund", "vip"]})
    assert _ids(tagged) == [refund]  # all tags (AND); KB entries without them are out
    by_meta = _retrieve(app_client, h, "refund", filters={"metadata": {"region": "cn"}})
    assert _ids(by_meta) == [refund]
    typed = _retrieve(app_client, h, "refund", sources=["memory"], memory_types=["episodic"])
    assert _ids(typed) == [episode]


def test_pinned_memories_come_first_unless_left_out(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, _ = app_client.new_tenant("acme")
    pinned = post_memory(app_client, h, "speaks German", pinned=True, user_id="u1")
    hit = post_memory(app_client, h, "refund pending", user_id="u1")
    app_client.drain()
    fix_order(monkeypatch, app_client.services.providers.memory, [hit])  # only `hit` matches
    scope = {"user_id": "u1"}
    items = _retrieve(app_client, h, "refund", scope=scope, sources=["memory"]).json()["items"]
    assert [(i["id"], i["pinned"]) for i in items] == [(pinned, True), (hit, False)]
    left_out = _retrieve(
        app_client, h, "refund", scope=scope, sources=["memory"], include_pinned=False
    )
    assert _ids(left_out) == [hit]  # not prepended (it can still come back as a search hit)


def test_near_duplicates_are_dropped_for_something_new(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, _ = app_client.new_tenant("acme")
    a = post_memory(app_client, h, "refunds take seven days after the return")
    copy = post_memory(app_client, h, "refunds take seven days after the return arrives")
    other = post_memory(app_client, h, "shipping is free above fifty euros")
    app_client.drain()
    fix_order(monkeypatch, app_client.services.providers.memory, [a, copy, other])
    assert _ids(_retrieve(app_client, h, "refunds", sources=["memory"], top_k=2)) == [a, other]


def test_the_token_budget_bounds_items_and_context(app_client: AppClient) -> None:
    h, _ = app_client.new_tenant("acme")
    long = post_memory(app_client, h, "refund " + "detail " * 200)  # ~470 tokens
    short = post_memory(app_client, h, "refund approved")
    app_client.drain()
    r = _retrieve(app_client, h, "refund", sources=["memory"], token_budget=50, format="context")
    assert _ids(r) == [short]  # the long one does not fit; the next that does is kept
    context = r.json()["context"]
    assert f"[mem:{short}]" in context and long not in context
    assert estimate_tokens(context) <= 50  # the rendered context itself fits
    assert len(_ids(_retrieve(app_client, h, "refund", sources=["memory"]))) == 2  # default 2000


def test_an_item_too_long_for_the_budget_gives_way_to_the_next_that_fits(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, _ = app_client.new_tenant("acme")
    long = post_memory(app_client, h, "refund " + "detail " * 200)
    first = post_memory(app_client, h, "refund approved")
    second = post_memory(app_client, h, "card refunded in full")
    third = post_memory(app_client, h, "store credit issued instead")
    app_client.drain()
    fix_order(monkeypatch, app_client.services.providers.memory, [long, first, second, third])
    r = _retrieve(app_client, h, "refund", sources=["memory"], top_k=2, token_budget=40)
    assert _ids(r) == [first, second]  # top_k is still reached from further down


def test_the_budget_counts_what_is_sent(app_client: AppClient) -> None:
    """format=context counts each rendered line (its citation id is ~12 tokens); items count
    their text. Pinned memories are within the budget too."""
    h, _ = app_client.new_tenant("acme")
    short = post_memory(app_client, h, "refund approved")  # 4 tokens of text, ~25 as a line
    app_client.drain()
    as_items = _retrieve(app_client, h, "refund", sources=["memory"], token_budget=10)
    assert _ids(as_items) == [short]
    as_context = _retrieve(
        app_client, h, "refund", sources=["memory"], token_budget=10, format="context"
    )
    assert _ids(as_context) == [] and as_context.json()["context"] == ""

    pinned = post_memory(app_client, h, "profile " + "note " * 200, pinned=True, user_id="u1")
    app_client.drain()
    r = _retrieve(
        app_client, h, "refund", scope={"user_id": "u1"}, sources=["memory"], token_budget=60
    )
    assert pinned not in _ids(r) and short in _ids(r)  # a pinned memory that does not fit


def test_rerank_is_refused_until_a_reranker_is_configured(app_client: AppClient) -> None:
    h, _ = app_client.new_tenant("acme")
    r = _retrieve(app_client, h, "refund", rerank=True)
    assert r.status_code == 422 and "rerank" in r.json()["detail"]


def test_only_the_source_a_filter_applies_to_is_asked_for_more(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, ns = app_client.new_tenant("acme")
    app_client.apply(CreateCollection(ns, "faq"))
    collection = app_client.apply(CreateCollection(ns, "faq2"))
    app_client.apply(kb_entry_op(ns, collection.id, KbStatus.ACTIVE))
    app_client.drain()
    asked: dict[str, int] = {}
    providers = app_client.services.providers
    for name in ("memory", "kb"):
        provider = getattr(providers, name)
        original = provider.search

        async def spy(*args: Any, _name: str = name, _search: Any = original, **kw: Any) -> Any:
            asked[_name] = kw["top_k"]
            return await _search(*args, **kw)

        monkeypatch.setattr(provider, "search", spy)
    _ids(_retrieve(app_client, h, "refund", top_k=4, memory_types=["episodic"]))
    assert asked == {"memory": 4 * 3 * 3, "kb": 12}  # memory types filter memories only
    _ids(_retrieve(app_client, h, "refund", top_k=4, filters={"tags": ["x"]}))
    assert asked == {"memory": 36, "kb": 36}  # tags filter both


def test_mmr_considers_at_most_60_candidates(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_steward.models import RetrievalLog

    h, _ = app_client.new_tenant("acme")
    ids = [post_memory(app_client, h, f"refund note number {i}") for i in range(70)]
    app_client.drain()
    fix_order(monkeypatch, app_client.services.providers.memory, ids)
    body = _retrieve(app_client, h, "refund", sources=["memory"], top_k=50).json()
    log = app_client.get(RetrievalLog, body["retrieval_id"])
    assert len(log.results) + len(log.left_out) == 60  # min(50 * 3, 60) considered


def test_filters_apply_to_pinned_memories_too(app_client: AppClient) -> None:
    h, _ = app_client.new_tenant("acme")
    profile = post_memory(app_client, h, "speaks German", pinned=True, user_id="u1")
    episode = post_memory(
        app_client, h, "refund call", memory_type="episodic", tags=["refund"], user_id="u1"
    )
    app_client.drain()
    scope = {"user_id": "u1"}
    typed = _ids(_retrieve(app_client, h, "refund", scope=scope, memory_types=["episodic"]))
    assert typed == [episode]  # the pinned semantic memory is not of the asked type
    tagged = _ids(_retrieve(app_client, h, "refund", scope=scope, filters={"tags": ["refund"]}))
    assert profile not in tagged and episode in tagged
