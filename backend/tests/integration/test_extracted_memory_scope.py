"""What a conversation teaches is the user's memory, not the session's (design §5 step 2): an
episode's session is provenance (and its cards' budget), never the scope of the memories extracted
from it. Both dialects, through the API; fake engine and FakeLLM."""

from collections.abc import Iterator
from typing import Any

import pytest

from tests.conftest import AppClient, running_app
from tests.fakes.llm import FakeLLM, user_lines
from tests.fakes.memory_provider import FakeMemoryProvider
from tests.integration.test_kb_suggestions import _cards, _collection, _llm

FACT = "I spent three months in Beijing"


def _episode(c: AppClient, h: dict[str, str], text: str, session: str) -> dict[str, Any]:
    body = {
        "messages": [{"role": "user", "content": text}],
        "user_id": "u1",
        "session_id": session,
        "mode": "sync",
    }
    r = c.http.post("/v1/episodes", json=body, headers=h)
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def _recalled(c: AppClient, h: dict[str, str], scope: dict[str, str]) -> list[str]:
    found = c.retrieve(h, query="Beijing", scope=scope, sources=["memory"])
    return [i["content"] for i in found["items"]]


def _assert_the_users_in_every_session(c: AppClient, h: dict[str, str]) -> None:
    assert _recalled(c, h, {"user_id": "u1", "session_id": "s2"}) == [FACT]
    assert _recalled(c, h, {"user_id": "u1"}) == [FACT]
    assert _recalled(c, h, {"user_id": "u2"}) == []  # still the user's own


@pytest.fixture
def engine_extracts(migrated_url: str) -> Iterator[tuple[AppClient, FakeMemoryProvider]]:
    engine = FakeMemoryProvider()
    with running_app(
        migrated_url, settings={"kb_llm": True}, memory_provider=engine, llm=_llm()
    ) as client:
        yield client, engine


@pytest.fixture
def steward_extracts(migrated_url: str) -> Iterator[AppClient]:
    llm = FakeLLM(memory_extract=user_lines())
    with running_app(migrated_url, settings={"memory_llm": True}, llm=llm) as client:
        yield client


def test_an_engine_extracted_memory_is_recalled_in_every_session(
    engine_extracts: tuple[AppClient, FakeMemoryProvider],
) -> None:
    c, engine = engine_extracts
    h = c.new_key("acme")
    [memory] = _episode(c, h, FACT, session="s1")["memories"]
    assert memory["session_id"] is None
    assert memory["user_id"] == "u1"
    # the engine was told the user's scope too, so its own search does not filter by session
    assert [r.scope.session_id for r in engine.records.values()] == [None]
    _assert_the_users_in_every_session(c, h)


def test_a_memory_llm_steward_extracts_is_recalled_in_every_session(
    steward_extracts: AppClient,
) -> None:
    c = steward_extracts
    h = c.new_key("acme")
    [memory] = _episode(c, h, FACT, session="s1")["memories"]
    assert memory["session_id"] is None
    c.drain()  # the Syncer pushes it to the engine
    _assert_the_users_in_every_session(c, h)


def test_a_promoted_extracted_memory_is_asked_about_in_its_conversation(
    engine_extracts: tuple[AppClient, FakeMemoryProvider],
) -> None:
    """The memory has no session, its episode does: the card shows where the user said it."""
    c, _ = engine_extracts
    h, ns = c.new_tenant("acme")
    [memory] = _episode(c, h, FACT, session="s1")["memories"]
    r = c.http.post(
        f"/v1/memories/{memory['id']}/promote",
        json={"collection_id": _collection(c, ns)},
        headers=h,
    )
    assert r.status_code == 202, r.text
    card = r.json()["candidate"]
    assert card["stage"] == "awaiting_user"
    assert [s["id"] for s in _cards(c, h, session="s1")] == [card["id"]]
    assert _cards(c, h, session="s2") == []


def _noop_on_same_text(payload: dict[str, Any]) -> dict[str, Any]:
    """memory.resolve: a fact said again is a duplicate (NOOP) of the same text."""
    same = [e for e in payload["EXISTING"] if e["fact"] == payload["NEW"]]
    if same:
        return {"action": "NOOP", "target_id": same[0]["id"], "reason": "said before"}
    return {"action": "ADD", "target_id": None, "reason": "new"}


def test_a_session_only_copy_does_not_swallow_the_users_memory(migrated_url: str) -> None:
    """Compensation compares a new memory with memories of its own scope: the same fact kept
    for one session only must not make the user's memory a duplicate (NOOP) and delete it."""
    llm = FakeLLM(memory_extract=user_lines(), memory_resolve=_noop_on_same_text)
    with running_app(migrated_url, settings={"memory_llm": True}, llm=llm) as c:
        h = c.new_key("acme")
        body = {"content": FACT, "user_id": "u1", "session_id": "s1"}
        assert c.http.post("/v1/memories", json=body, headers=h).status_code == 201
        c.drain()
        _episode(c, h, FACT, session="s2")
        c.drain()
        assert _recalled(c, h, {"user_id": "u1", "session_id": "s3"}) == [FACT]
