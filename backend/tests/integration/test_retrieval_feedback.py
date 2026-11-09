"""The retrieval record, feedback and hit statistics (design §6 step 9, §7; P4e), both dialects."""

import uuid
from typing import Any

import pytest
from sqlalchemy import select

from llm_steward.models import KbEntry, Memory, RetrievalFeedback, RetrievalLog
from llm_steward.models.enums import KbStatus
from llm_steward.services.governance_service import CreateCollection
from llm_steward.storage.db import session_factory
from tests.conftest import AppClient, fix_order, kb_entry_op, post_memory


def _retrieve(c: AppClient, h: dict[str, str], **body: Any) -> dict[str, Any]:
    r = c.http.post("/v1/retrieve", json={"query": "refund", **body}, headers=h)
    assert r.status_code == 200, r.text
    return dict(r.json())


def _setup(c: AppClient) -> tuple[dict[str, str], uuid.UUID, str, str]:
    h, ns = c.new_tenant("acme")
    memory = post_memory(c, h, "refund pending for order 7")
    collection = c.apply(CreateCollection(ns, "faq"))
    entry = c.apply(kb_entry_op(ns, collection.id, KbStatus.ACTIVE))
    c.drain()
    return h, ns, memory, str(entry.id)


def _count_hits(c: AppClient) -> int:
    return c.call(c.services.retrieval.count_hits)


def test_the_log_keeps_each_items_rank_and_scores(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, _, memory, entry = _setup(app_client)
    providers = app_client.services.providers
    fix_order(monkeypatch, providers.memory, [memory])
    fix_order(monkeypatch, providers.kb, [entry])
    body = _retrieve(app_client, h)
    log = app_client.get(RetrievalLog, body["retrieval_id"])
    by_id = {r["id"]: r for r in log.results}
    assert set(by_id) == {memory, entry}  # what was returned, nothing else
    assert (by_id[memory]["rank"], by_id[entry]["rank"]) == (1, 1)  # within its source
    assert by_id[memory]["rel"] == pytest.approx(1 / 61)  # fused relevance, before weights
    returned = {i["id"]: i["score"] for i in body["items"]}
    assert by_id[entry]["score"] == pytest.approx(returned[entry])
    assert (by_id[entry]["kind"], by_id[memory]["kind"]) == ("entry", "memory")


def test_candidates_left_out_are_logged_apart_with_why(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    h, _ = app_client.new_tenant("acme")
    long = post_memory(app_client, h, "refund " + "detail " * 200)
    short = [post_memory(app_client, h, f"refund note {i}") for i in range(3)]
    app_client.drain()
    fix_order(monkeypatch, app_client.services.providers.memory, [long, *short])
    body = _retrieve(app_client, h, sources=["memory"], top_k=2, token_budget=40)
    log = app_client.get(RetrievalLog, body["retrieval_id"])
    left_out = {r["id"]: r["why"] for r in log.left_out}
    assert left_out[long] == "budget"
    returned = {i["id"] for i in body["items"]}
    assert set(left_out) == {long, *short} - returned
    assert set(left_out.values()) <= {"budget", "rank"}
    assert {r["id"] for r in log.results} == returned
    # a candidate that was left out was never shown: no feedback on it
    r = app_client.http.post(
        f"/v1/retrieve/{body['retrieval_id']}/feedback",
        json={"item_id": long, "helpful": True},
        headers=h,
    )
    assert r.status_code == 422


def test_hits_are_counted_in_batches_off_the_request_path(app_client: AppClient) -> None:
    h, _, memory, entry = _setup(app_client)
    before = app_client.get(KbEntry, entry).updated_at
    returned = {i["id"] for i in _retrieve(app_client, h)["items"]}
    _retrieve(app_client, h)
    assert returned == {memory, entry}
    assert app_client.get(Memory, memory).access_count == 0  # not on the request path
    assert _count_hits(app_client) == 2  # two retrievals counted in one pass
    seen = app_client.get(Memory, memory)
    assert seen.access_count == 2 and seen.last_accessed_at is not None
    counted = app_client.get(KbEntry, entry)
    assert counted.hit_count == 2
    assert counted.updated_at == before  # being read is not being edited
    assert _count_hits(app_client) == 0  # each retrieval is counted once
    assert app_client.get(KbEntry, entry).hit_count == 2


def test_feedback_is_recorded_and_counted_on_knowledge(app_client: AppClient) -> None:
    h, ns, memory, entry = _setup(app_client)
    rid = _retrieve(app_client, h)["retrieval_id"]
    note = "wrong for bob@example.com"
    for item_id, helpful in ((entry, False), (entry, True), (memory, True)):
        r = app_client.http.post(
            f"/v1/retrieve/{rid}/feedback",
            json={"item_id": item_id, "helpful": helpful, "note": note},
            headers=h,
        )
        assert r.status_code == 204, r.text
    counted = app_client.get(KbEntry, entry)  # at once: feedback is rare, no queue
    assert (counted.helpful_count, counted.unhelpful_count) == (1, 1)

    async def rows() -> list[RetrievalFeedback]:
        async with session_factory(app_client.services.engine)() as s:
            query = select(RetrievalFeedback).where(RetrievalFeedback.namespace_id == ns)
            return list(await s.scalars(query.order_by(RetrievalFeedback.id)))

    given = app_client.call(rows)
    assert [(str(f.item_id), f.kind, f.helpful) for f in given] == [
        (entry, "entry", False),
        (entry, "entry", True),
        (memory, "memory", True),
    ]
    assert all(str(f.retrieval_id) == rid for f in given)
    assert all(f.note and "bob@example.com" not in f.note for f in given)  # redacted


def test_feedback_is_checked_against_the_retrieval(app_client: AppClient) -> None:
    h, _, memory, _ = _setup(app_client)
    rid = _retrieve(app_client, h)["retrieval_id"]
    stranger = str(uuid.uuid4())
    url = f"/v1/retrieve/{rid}/feedback"
    r = app_client.http.post(url, json={"item_id": stranger, "helpful": True}, headers=h)
    assert r.status_code == 422  # not one of that retrieval's items
    other = app_client.new_key("other")
    r = app_client.http.post(url, json={"item_id": memory, "helpful": True}, headers=other)
    assert r.status_code == 404  # another namespace's retrieval
    missing = f"/v1/retrieve/{uuid.uuid4()}/feedback"
    r = app_client.http.post(missing, json={"item_id": memory, "helpful": True}, headers=h)
    assert r.status_code == 404
