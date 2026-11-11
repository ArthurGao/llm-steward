"""S9 (lite-mem): an engine that resolves conflicts itself reports what it replaced or retired, and
llm-steward mirrors those decisions as its own governance operations — in the same transaction as
the new memory — so the history stays complete without running its own compensation."""

from collections.abc import Iterator
from typing import Any

import pytest

from llm_steward.providers.base import RESOLVES_CONFLICTS, MemoryEvent, Message, Scope
from tests.conftest import AppClient, running_app
from tests.fakes.llm import FakeLLM
from tests.fakes.memory_provider import FakeMemoryProvider, _Record

DECIDED_BY = "lite-mem:fake-resolver"


class ResolvingEngine(FakeMemoryProvider):
    """Like lite-mem: "搬" (moved) supersedes the user's oldest fact, "不再" (no longer) retires it;
    every inferred add carries the caller's idempotency key."""

    name = "resolving"
    capabilities = FakeMemoryProvider.capabilities | {RESOLVES_CONFLICTS}

    def __init__(self) -> None:
        super().__init__()
        self.keys: list[str | None] = []

    async def add(
        self,
        messages: list[Message],
        scope: Scope,
        *,
        infer: bool = True,
        metadata: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> list[MemoryEvent]:
        if not infer:
            return await super().add(messages, scope, infer=False, metadata=metadata)
        self.keys.append(idempotency_key)
        said = " ".join(m.content for m in messages if m.role == "user")
        mine = [eid for eid, r in self.records.items() if r.scope.user_id == scope.user_id]
        if "不再" in said and mine:
            self.records.pop(mine[0])
            return [MemoryEvent("DELETE", mine[0], None, DECIDED_BY)]
        events = await super().add(messages, scope, infer=True)
        if "搬" in said and mine:
            self.records.pop(mine[0])
            [new] = events
            return [MemoryEvent("ADD", new.external_id, new.content, DECIDED_BY, mine[0])]
        return events


@pytest.fixture
def resolving(migrated_url: str) -> Iterator[tuple[AppClient, ResolvingEngine, FakeLLM]]:
    engine, llm = ResolvingEngine(), FakeLLM()  # no answers: any compensation call would fail
    with running_app(migrated_url, memory_provider=engine, llm=llm) as client:
        yield client, engine, llm


def _episode(c: AppClient, h: dict[str, str], text: str) -> dict[str, Any]:
    body = {"messages": [{"role": "user", "content": text}], "user_id": "u1", "mode": "sync"}
    response = c.http.post("/v1/episodes", json=body, headers=h)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def test_a_superseding_fact_supersedes_the_mirror_too(
    resolving: tuple[AppClient, ResolvingEngine, FakeLLM],
) -> None:
    c, engine, llm = resolving
    h = c.new_key("acme")
    [beijing] = _episode(c, h, "我住北京")["memories"]
    moved = _episode(c, h, "我搬到上海了")
    [shanghai] = moved["memories"]
    assert c.http.get(f"/v1/memories/{beijing['id']}", headers=h).json()["status"] == "superseded"
    history = c.http.get(f"/v1/memories/{beijing['id']}/history", headers=h).json()
    assert [m["id"] for m in history["chain"]] == [beijing["id"], shanghai["id"]]
    change = history["changes"][-1]
    assert (change["actor_type"], change["actor_id"]) == ("ai", DECIDED_BY)
    active = c.http.get("/v1/memories?user_id=u1", headers=h).json()["items"]
    assert [m["content"] for m in active] == ["我搬到上海了"]
    assert llm.calls == []  # the engine decided: no compensation
    assert engine.keys[-1] == moved["episode_id"]  # the batch's last episode is the key


def test_a_retired_fact_is_retired_in_the_mirror(
    resolving: tuple[AppClient, ResolvingEngine, FakeLLM],
) -> None:
    c, _, _ = resolving
    h = c.new_key("acme")
    [coffee] = _episode(c, h, "我喜欢咖啡")["memories"]
    assert _episode(c, h, "我不再喜欢咖啡")["memories"] == []
    assert c.http.get(f"/v1/memories/{coffee['id']}", headers=h).json()["status"] == "superseded"
    history = c.http.get(f"/v1/memories/{coffee['id']}/history", headers=h).json()
    assert [m["id"] for m in history["chain"]] == [coffee["id"]]  # retired: no successor
    assert c.http.get("/v1/memories?user_id=u1", headers=h).json()["items"] == []


def test_a_replaced_id_with_no_mirror_still_records_the_new_fact(
    resolving: tuple[AppClient, ResolvingEngine, FakeLLM],
) -> None:
    """The engine may name a memory llm-steward never mirrored: the new fact is kept, the unknown
    id is skipped (the retrieval safety net ignores unmapped ids anyway)."""
    c, engine, _ = resolving
    h = c.new_key("acme")
    [first] = _episode(c, h, "我喜欢绿茶")["memories"]
    namespace = engine.records[next(iter(engine.records))].scope.namespace_id
    engine.records.clear()  # the engine lost it; it holds one llm-steward never saw instead
    engine.records["stray"] = _Record("我住北京", Scope(namespace, user_id="u1"))
    [kept] = _episode(c, h, "我搬到上海了")["memories"]
    assert kept["status"] == "active"
    assert c.http.get(f"/v1/memories/{first['id']}", headers=h).json()["status"] == "active"


def test_the_engine_retiring_what_was_already_invalidated_changes_nothing(
    resolving: tuple[AppClient, ResolvingEngine, FakeLLM],
) -> None:
    """A replayed or late DELETE for a memory llm-steward already invalidated (here: the user
    deleted it) is skipped, not applied twice."""
    c, _, _ = resolving
    h = c.new_key("acme")
    [coffee] = _episode(c, h, "我喜欢咖啡")["memories"]
    assert c.http.delete(f"/v1/memories/{coffee['id']}", headers=h).status_code in (200, 204)
    assert _episode(c, h, "我不再喜欢咖啡")["memories"] == []
    history = c.http.get(f"/v1/memories/{coffee['id']}/history", headers=h).json()
    assert [ch["actor_type"] for ch in history["changes"]].count("ai") == 1  # its creation only
