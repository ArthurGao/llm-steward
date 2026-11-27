"""New knowledge that touches effective knowledge goes to a reviewer (design §5 "KB review
policy"). Found in glassdesk's browser test (2026-11-27): a member accepted a card saying the
expense deadline is 90 days, and it went live beside the active entry saying 30. Both dialects,
through the HTTP API; FakeLLM scripts the `kb.similar` vectors."""

import hashlib
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from typing import Any

import pytest

from tests.conftest import Tenant, running_app
from tests.fakes.llm import FakeLLM, update_when
from tests.fakes.memory_provider import FakeMemoryProvider

DIM = 64
TOPIC = ("expense", "报销")  # texts naming it embed onto one axis; all others away from it


def _vector(text: str) -> list[float]:
    if any(word in text.lower() for word in TOPIC):
        return [1.0] + [0.0] * (DIM - 1)
    digest = hashlib.sha256(text.encode()).digest()
    return [0.0] + [(digest[i % len(digest)] - 127.5) / 127.5 for i in range(DIM - 1)]


def _llm(similar: Any = _vector, risk: str = "medium") -> FakeLLM:
    def draft(payload: dict[str, Any]) -> dict[str, Any]:
        text = payload["TEXT"]
        return {"title": text[:20], "content": text, "risk_level": risk, "reason": "a process"}

    return FakeLLM(
        memory_resolve=update_when("never"),
        kb_draft=draft,
        kb_risk=lambda p: {"risk_level": risk, "reason": "process"},
        kb_worthiness=lambda p: {"items": []},
        similar=similar,
    )


@pytest.fixture
def make(migrated_url: str) -> Iterator[Callable[..., Tenant]]:
    """`make(**llm)`: a tenant in a running app whose FakeLLM is `_llm(**llm)`."""
    with ExitStack() as stack:

        def tenant(**llm: Any) -> Tenant:
            app = running_app(
                migrated_url,
                settings={"kb_llm": True},
                memory_provider=FakeMemoryProvider(),
                llm=_llm(**llm),
            )
            return Tenant(stack.enter_context(app), "acme")

        yield tenant


@pytest.fixture
def t(make: Callable[..., Tenant]) -> Tenant:
    return make()


def _entry(t: Tenant, collection: str, title: str, content: str, *, drain: bool = True) -> str:
    """Effective knowledge, written by a reviewer and (unless `drain=False`) synced to the KB
    engine."""
    body = {"collection_id": collection, "title": title, "content": content}
    r = t.post("/v1/kb/entries", body, as_reviewer=True)
    assert r.status_code == 201, r.text
    if drain:
        t.c.drain()
    return str(r.json()["entry"]["id"])


def _suggest(t: Tenant, collection: str, text: str, session: str = "s1") -> dict[str, Any]:
    body = {"collection_id": collection, "text": text, "user_id": "u1", "session_id": session}
    r = t.post("/v1/suggestions", body)
    assert r.status_code == 202, r.text
    return r.json()["candidate"]  # type: ignore[no-any-return]


def _expense(t: Tenant) -> tuple[str, str]:
    collection = t.collection()
    return collection, _entry(t, collection, "Expense claims", "Submit within 30 days.")


def test_a_draft_touching_an_effective_entry_goes_to_review(t: Tenant) -> None:
    collection, entry = _expense(t)
    card = _suggest(t, collection, "The expense claim deadline is 90 days.")
    assert card["stage"] == "awaiting_review"  # not the user's to accept, although one was named
    assert f"touches entry {entry} (similarity 1.0)" in card["reason"]
    assert card["draft"]["touches"] == [{"entry_id": entry, "similarity": 1.0}]


def test_a_members_own_entry_touching_an_effective_one_goes_to_review(t: Tenant) -> None:
    # the same contradiction typed by hand instead of drafted
    collection, entry = _expense(t)
    r = t.submit(collection, "Expense claims are due within 90 days.", title="Expenses")
    assert r.status_code == 202, r.text
    card = r.json()["candidate"]
    assert card["stage"] == "awaiting_review"
    assert f"touches entry {entry}" in card["reason"]
    body = {"collection_id": collection, "title": "Expense v2", "content": "Expenses: 45 days."}
    reviewer = t.post("/v1/kb/entries", body, as_reviewer=True)
    assert reviewer.status_code == 201, reviewer.text  # a reviewer's own write still goes in


def test_a_draft_about_something_else_is_still_the_users_card(t: Tenant) -> None:
    collection, _ = _expense(t)
    card = _suggest(t, collection, "Meeting rooms are booked for two hours at most.")
    assert card["stage"] == "awaiting_user"
    assert "touches" not in card["draft"]


def test_a_check_that_cannot_run_counts_as_touching(make: Callable[..., Tenant]) -> None:
    # unknown is not "no conflict": a failed embedding sends the draft to a reviewer
    t = make(similar=RuntimeError("embedding endpoint down"))
    collection = t.collection()
    _entry(t, collection, "Meeting rooms", "Book rooms for two hours at most.")
    card = _suggest(t, collection, "Rooms can be booked for three hours.")
    assert card["stage"] == "awaiting_review"
    assert "could not check" in card["reason"]
    assert card["draft"]["touches"] == [{"entry_id": None, "similarity": None}]


def test_the_same_suggestion_twice_in_a_session_is_one_card(t: Tenant) -> None:
    collection = t.collection()
    first = _suggest(t, collection, "The expense claim deadline is 90 days.")
    again = _suggest(t, collection, "Expense claim deadline updated: 90 days.")
    assert again["id"] == first["id"]
    other_session = _suggest(t, collection, "Expense deadline: 90 days.", session="s2")
    assert other_session["id"] != first["id"]  # another conversation asks on its own
    pending = t.c.http.get("/v1/candidates", headers=t.reviewer).json()["items"]
    assert len(pending) == 2


def test_accepting_a_card_that_now_touches_an_entry_sends_it_to_review(t: Tenant) -> None:
    # the card was drafted before the entry existed: accepting it is checked again
    collection = t.collection()
    card = _suggest(t, collection, "The expense claim deadline is 90 days.")
    assert card["stage"] == "awaiting_user"
    entry = _entry(t, collection, "Expense claims", "Submit within 30 days.")
    r = t.post(f"/v1/candidates/{card['id']}/accept", {"user_id": "u1"})
    assert r.status_code == 200, r.text
    decided = r.json()
    assert (decided["status"], decided["stage"]) == ("pending", "awaiting_review")
    assert decided["entry"] is None  # nothing went live
    assert decided["draft"]["touches"] == [{"entry_id": entry, "similarity": 1.0}]
    assert f"touches entry {entry}" in decided["reason"]  # the reviewer sees why, as on submit


def _edge(text: str) -> list[float]:
    """Near the threshold (0.63 by default): "edge-hi" at exactly 0.63 from the expense axis,
    "edge-lo" at 0.629; the axis itself for expense texts."""
    for marker, x in (("edge-hi", 0.63), ("edge-lo", 0.629)):
        if marker in text:
            return [x, (1 - x * x) ** 0.5] + [0.0] * (DIM - 2)
    return _vector(text)


def test_the_threshold_is_the_configured_one_and_inclusive(make: Callable[..., Tenant]) -> None:
    t = make(similar=_edge)
    collection, entry = _expense(t)
    at = _suggest(t, collection, "edge-hi: claims change", session="a")
    below = _suggest(t, collection, "edge-lo: claims change", session="b")
    assert (at["stage"], below["stage"]) == ("awaiting_review", "awaiting_user")
    assert at["draft"]["touches"] == [{"entry_id": entry, "similarity": 0.63}]


def test_a_waiting_card_is_reused_only_for_the_same_user_and_collection(t: Tenant) -> None:
    # session ids are the host's strings: two users (or collections) may share one
    collection, other = t.collection(), t.collection()
    mine = _suggest(t, collection, "The expense claim deadline is 90 days.")
    body = {
        "collection_id": collection,
        "text": "Expense deadline: 90 days.",
        "user_id": "u2",
        "session_id": "s1",
    }
    theirs = t.post("/v1/suggestions", body).json()["candidate"]
    elsewhere = _suggest(t, other, "Expense deadline: 90 days.")
    assert len({mine["id"], theirs["id"], elsewhere["id"]}) == 3
    assert theirs["requested_for_user"] == "u2"


def test_effective_knowledge_not_yet_synced_counts_as_may_touch(t: Tenant) -> None:
    # the engine cannot find what the outbox has not pushed yet: unknown, so a reviewer decides
    collection = t.collection()
    _entry(t, collection, "Meeting rooms", "Book rooms for two hours at most.", drain=False)
    card = _suggest(t, collection, "Rooms can be booked for three hours.")
    assert card["stage"] == "awaiting_review"
    assert "could not check" in card["reason"]


def test_an_embedding_reply_that_does_not_fit_counts_as_may_touch(
    make: Callable[..., Tenant],
) -> None:
    t = make(similar=lambda text: [1.0] if "Expense" in text else [1.0, 0.0])  # mismatched sizes
    collection, _ = _expense(t)
    card = _suggest(t, collection, "Claims are due in 90 days.")
    assert card["stage"] == "awaiting_review"  # not a 500
