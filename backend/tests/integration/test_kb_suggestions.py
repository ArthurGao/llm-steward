"""P3b on both dialects, through the HTTP API: worthiness after extraction, LLM risk labels,
promote, suggestions and cards (design §5 steps 5-6). Fake engine, FakeLLM: never a real model."""

import uuid
from collections.abc import Iterator
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import update

from llm_steward.models import Candidate
from llm_steward.models.base import utcnow
from llm_steward.models.enums import ApiScope
from llm_steward.pipeline.kb_llm import CRITERIA
from tests.conftest import AppClient, running_app
from tests.fakes.llm import FakeLLM, update_when
from tests.fakes.memory_provider import FakeMemoryProvider

GENERAL = "Refunds"  # facts mentioning it score as general knowledge; others as personal


def _worthiness(payload: dict[str, Any]) -> dict[str, Any]:
    def scores(fact: dict[str, str]) -> dict[str, Any]:
        return {
            "id": fact["id"],
            **dict.fromkeys(CRITERIA, 0.9 if GENERAL in fact["fact"] else 0.2),
        }

    return {"items": [scores(f) for f in payload["FACTS"]]}


def _drafter(risk: str) -> Any:
    def draft(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            "title": "Refund time",
            "content": f"In general: {payload['TEXT']}",
            "risk_level": risk,
            "reason": "a process fact",
        }

    return draft


def _risk(level: str) -> Any:
    return lambda p: {"risk_level": level, "reason": f"looks {level}"}


def _llm(risk: str = "medium") -> FakeLLM:
    return FakeLLM(
        memory_resolve=update_when("never"),
        kb_worthiness=_worthiness,
        kb_draft=_drafter(risk),
        kb_risk=_risk(risk),
    )


@pytest.fixture
def app(migrated_url: str) -> Iterator[AppClient]:
    with running_app(
        migrated_url,
        settings={"kb_llm": True},
        memory_provider=FakeMemoryProvider(),
        llm=_llm(),
    ) as client:
        yield client


def _episode(c: AppClient, h: dict[str, str], text: str, session: str = "s1") -> dict[str, Any]:
    body = {
        "messages": [{"role": "user", "content": text}],
        "user_id": "u1",
        "session_id": session,
        "mode": "sync",
    }
    r = c.http.post("/v1/episodes", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _cards(c: AppClient, h: dict[str, str], session: str = "s1") -> list[dict[str, Any]]:
    body = {"query": "refunds", "scope": {"user_id": "u1", "session_id": session}}
    r = c.http.post("/v1/retrieve", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()["suggestions"]  # type: ignore[no-any-return]


def test_a_general_fact_from_a_conversation_becomes_a_card_for_the_user(app: AppClient) -> None:
    h = app.new_key("acme")
    _episode(app, h, "Refunds take 5 business days. I live in Berlin.")
    app.drain()  # the kb.suggest job
    [card] = _cards(app, h)
    assert (card["stage"], card["requested_for_user"], card["origin"]) == (
        "awaiting_user",
        "u1",
        "extraction",
    )
    assert card["draft"]["content"] == "In general: Refunds take 5 business days"
    assert card["draft"]["author_type"] == "ai"
    assert "drafted by kb.draft:fake-model: a process fact" in card["reason"]
    collections = app.http.get("/v1/kb/collections", headers=h).json()["items"]
    assert [c["name"] for c in collections] == ["inbox"]  # created on first use
    # the card shows on the next episode response too, and accepting it makes knowledge
    assert [s["id"] for s in _episode(app, h, "thanks")["suggestions"]] == [card["id"]]
    accepted = app.http.post(
        f"/v1/candidates/{card['id']}/accept", json={"user_id": "u1"}, headers=h
    )
    assert accepted.json()["entry"]["status"] == "active"


def test_scoring_a_memory_twice_never_makes_two_cards(app: AppClient) -> None:
    h, ns = app.new_tenant("acme")
    _episode(app, h, "Refunds take 5 business days.")
    app.drain()
    app.call(app.services.kb.suggest_from_memories, ns, app.call(_memory_ids, app, ns))
    assert len(_cards(app, h)) == 1


def test_at_most_two_cards_per_session(app: AppClient) -> None:
    h = app.new_key("acme")
    _episode(app, h, "Refunds take 5 days. Refunds need a receipt. Refunds go to the card.")
    app.drain()
    made = app.http.get("/v1/candidates?user_id=u1", headers=h).json()["items"]
    assert len(made) == 2  # three worthy facts; the third was not asked (do not disturb)
    assert len(_cards(app, h)) == 2
    assert _cards(app, h, session="s2") == []  # cards belong to their session


def test_promote_drafts_a_memory_into_knowledge(app: AppClient) -> None:
    h, ns = app.new_tenant("acme")
    memory = app.http.post("/v1/memories", json={"content": "my refund took 5 days"}, headers=h)
    collection = _collection(app, ns)
    r = app.http.post(
        f"/v1/memories/{memory.json()['id']}/promote",
        json={"collection_id": collection},
        headers=h,
    )
    # a model wrote it and nobody owns the memory to ask: a reviewer sees it before it goes live
    assert r.status_code == 202, r.text
    candidate = r.json()["candidate"]
    assert (candidate["stage"], candidate["origin"]) == ("awaiting_review", "promote")
    draft = candidate["draft"]
    assert draft["content"] == "In general: my refund took 5 days"
    assert (draft["origin"], draft["author_type"]) == ("promoted_memory", "ai")
    assert {"type": "memory", "id": memory.json()["id"]} in draft["source_refs"]


def test_a_suggestion_from_the_host_app_is_drafted_and_routed(app: AppClient) -> None:
    h, ns = app.new_tenant("acme")
    body = {
        "collection_id": _collection(app, ns),
        "text": "refunds now take 7 days",
        "user_id": "u1",
    }
    r = app.http.post("/v1/suggestions", json=body, headers=h)
    assert r.status_code == 202, r.text
    card = r.json()["candidate"]
    assert (card["stage"], card["origin"]) == ("awaiting_user", "user_suggestion")


def test_the_llm_risk_label_can_only_raise_the_risk(migrated_url: str) -> None:
    with running_app(
        migrated_url,
        settings={"kb_llm": True},
        memory_provider=FakeMemoryProvider(),
        llm=_llm("high"),
    ) as c:
        h, ns = c.new_tenant("acme", ApiScope.READ, ApiScope.WRITE)
        body = {"collection_id": _collection(c, ns), "title": "Plan", "content": "Gold costs 99"}
        r = c.http.post("/v1/kb/entries", json={**body, "risk_level": "low"}, headers=h)
        assert r.status_code == 202, r.text
        assert r.json()["candidate"]["stage"] == "awaiting_review"
        assert "kb.risk:fake-model: looks high" in r.json()["candidate"]["reason"]
        # a draft labels its own risk: high goes to a reviewer, even with a user to ask
        body = {"collection_id": _collection(c, ns), "text": "gold costs 99", "user_id": "u1"}
        drafted = c.http.post("/v1/suggestions", json=body, headers=h).json()["candidate"]
        assert drafted["stage"] == "awaiting_review"


def test_without_the_kb_llm_nothing_is_drafted_and_drafting_routes_say_so(
    migrated_url: str,
) -> None:
    with running_app(
        migrated_url,
        memory_provider=FakeMemoryProvider(),
        llm=FakeLLM(memory_resolve=update_when("never")),
    ) as c:
        h, ns = c.new_tenant("acme")
        _episode(c, h, "Refunds take 5 business days.")
        c.drain()
        assert _cards(c, h) == []
        body = {"collection_id": _collection(c, ns), "text": "x"}
        r = c.http.post("/v1/suggestions", json=body, headers=h)
        assert r.status_code == 422 and "STEWARD_KB_LLM" in r.text


def test_unanswered_cards_expire(app: AppClient) -> None:
    h = app.new_key("acme")
    _episode(app, h, "Refunds take 5 business days.")
    app.drain()
    [card] = _cards(app, h)
    app.call(_age, app, uuid.UUID(card["id"]))
    assert app.call(app.services.governance.expire_candidates) == 1
    assert _cards(app, h) == []
    status = app.http.get("/v1/candidates?status=expired", headers=h).json()["items"]
    assert [c["id"] for c in status] == [card["id"]]


def test_the_llm_label_never_lowers_the_callers_estimate(app: AppClient) -> None:
    h, ns = app.new_tenant("acme")
    body = {"collection_id": _collection(app, ns), "title": "Plan", "content": "Gold costs 99"}
    r = app.http.post("/v1/kb/entries", json={**body, "risk_level": "high"}, headers=h)
    assert r.status_code == 202 and r.json()["candidate"]["risk_level"] == "high"  # model: medium


def test_edits_get_the_llm_label_too(migrated_url: str) -> None:
    """An edit (PATCH) and an edited card are labelled like a submission: the model's "high" sends
    both to a reviewer instead of going live."""
    with running_app(
        migrated_url,
        settings={"kb_llm": True},
        memory_provider=FakeMemoryProvider(),
        llm=_llm("high"),
    ) as c:
        h, ns = c.new_tenant("acme")
        entry = c.apply(_active_entry(ns, _collection(c, ns)))
        r = c.http.patch(f"/v1/kb/entries/{entry.id}", json={"content": "Gold costs 99"}, headers=h)
        assert r.status_code == 202 and r.json()["candidate"]["stage"] == "awaiting_review"

        card = c.apply(_card(ns, _collection(c, ns)))
        edit = {"user_id": "alice", "draft": {"content": "Gold costs 99"}}
        moved = c.http.post(f"/v1/candidates/{card.id}/accept", json=edit, headers=h).json()
        assert (moved["stage"], moved["risk_level"], moved["entry"]) == (
            "awaiting_review",
            "high",
            None,
        )


# --- helpers ------------------------------------------------------------------------------------


def _collection(c: AppClient, ns: uuid.UUID) -> str:
    from llm_steward.services.governance_service import CreateCollection

    return str(c.apply(CreateCollection(ns, f"c-{uuid.uuid4().hex[:6]}")).id)


async def _memory_ids(c: AppClient, ns: uuid.UUID) -> list[uuid.UUID]:
    from sqlalchemy import select

    from llm_steward.models import Memory
    from llm_steward.storage.db import session_factory

    async with session_factory(c.services.engine)() as s:
        return list(await s.scalars(select(Memory.id).where(Memory.namespace_id == ns)))


async def _age(c: AppClient, candidate_id: uuid.UUID) -> None:
    from llm_steward.storage.db import session_factory

    async with session_factory(c.services.engine)() as s, s.begin():
        await s.execute(
            update(Candidate)
            .where(Candidate.id == candidate_id)
            .values(expires_at=utcnow() - timedelta(minutes=1))
        )


def _active_entry(ns: uuid.UUID, collection: str) -> Any:
    from llm_steward.models.enums import KbStatus
    from tests.conftest import kb_entry_op

    return kb_entry_op(ns, uuid.UUID(collection), KbStatus.ACTIVE)


def _card(ns: uuid.UUID, collection: str) -> Any:
    from llm_steward.models.enums import (
        CandidateOp,
        CandidateOrigin,
        CandidateStage,
        CandidateTarget,
        RiskLevel,
    )
    from llm_steward.services.governance_service import CreateCandidate

    return CreateCandidate(
        namespace_id=ns,
        target_type=CandidateTarget.KB_ENTRY,
        op=CandidateOp.ADD,
        draft={"collection_id": collection, "title": "Plan", "content": "Gold plan"},
        risk_level=RiskLevel.MEDIUM,
        origin=CandidateOrigin.USER_SUGGESTION,
        stage=CandidateStage.AWAITING_USER,
        requested_for_user="alice",
    )


def _reject(c: AppClient, h: dict[str, str], card: dict[str, Any], **body: Any) -> Any:
    return c.http.post(
        f"/v1/candidates/{card['id']}/reject", json={"user_id": "u1", **body}, headers=h
    )


def _memories(c: AppClient, h: dict[str, str]) -> list[dict[str, Any]]:
    return c.http.get("/v1/memories?user_id=u1", headers=h).json()["items"]  # type: ignore[no-any-return]


def test_no_thanks_and_stop_suggesting_ends_the_users_cards_until_undone(app: AppClient) -> None:
    """Design §5 "do not ask me again", per user (facts carry no finer category yet: extracted
    memories are all `semantic`): a procedural memory of theirs, visible and deletable to undo."""
    h = app.new_key("acme")
    _episode(app, h, "Refunds take 5 business days.")
    app.drain()
    [card] = _cards(app, h)
    assert _reject(app, h, card, stop_suggestions=True).status_code == 200

    [rule] = [m for m in _memories(app, h) if m["memory_type"] == "procedural"]
    assert "suggest" in rule["content"]
    _episode(app, h, "Refunds need a receipt.", session="s2")
    app.drain()
    assert _cards(app, h, session="s2") == []  # no more cards for this user

    assert app.http.delete(f"/v1/memories/{rule['id']}", headers=h).status_code == 200
    _episode(app, h, "Refunds go back to the card.", session="s3")
    app.drain()
    assert len(_cards(app, h, session="s3")) == 1  # undone: asked again


def test_stopping_twice_keeps_one_rule(app: AppClient) -> None:
    h = app.new_key("acme")
    _episode(app, h, "Refunds take 5 days. Refunds need a receipt.")
    app.drain()
    first, second = _cards(app, h)
    assert _reject(app, h, first, stop_suggestions=True).status_code == 200
    assert _reject(app, h, second, stop_suggestions=True).status_code == 200
    rules = [m for m in _memories(app, h) if m["memory_type"] == "procedural"]
    assert len(rules) == 1


def test_a_stopped_users_facts_are_not_even_scored(app: AppClient) -> None:
    """No LLM call is paid for facts that can never become a card."""
    h = app.new_key("acme")
    _episode(app, h, "Refunds take 5 business days.")
    app.drain()
    [card] = _cards(app, h)
    _reject(app, h, card, stop_suggestions=True)
    llm = app.services.kb._llm
    scored = [c for c in llm.calls if c[0] == "kb.worthiness"]
    _episode(app, h, "Refunds need a receipt.", session="s2")
    app.drain()
    assert [c for c in llm.calls if c[0] == "kb.worthiness"] == scored


def test_a_card_asked_of_no_user_has_no_suggestions_to_stop(app: AppClient) -> None:
    h, ns = app.new_tenant("acme")
    reviewer = app.key("acme", ApiScope.READ, ApiScope.WRITE, ApiScope.REVIEW)
    body = {"collection_id": _collection(app, ns), "title": "Refunds", "content": "5 days"}
    queued = app.http.post("/v1/kb/entries", json={**body, "risk_level": "high"}, headers=h)
    card = queued.json()["candidate"]
    assert card["requested_for_user"] is None  # the review queue's, not a user's
    r = app.http.post(
        f"/v1/candidates/{card['id']}/reject",
        json={"stop_suggestions": True, "note": "no"},
        headers=reviewer,
    )
    assert r.status_code == 422, r.text
    assert app.http.get("/v1/candidates", headers=reviewer).json()["items"][0]["status"] == (
        "pending"
    )  # refused before anything was decided
