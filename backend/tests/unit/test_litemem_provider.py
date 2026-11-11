"""LiteMemProvider (S9) over a fake transport: the mapping between llm-steward and lite-mem — ids,
namespaces, scopes, events, keys — and the HTTP transport's status handling."""

import uuid
from typing import Any

import httpx2
import pytest

from llm_steward.errors import ProviderUnavailable, Unprocessable
from llm_steward.providers.base import (
    DELETE,
    EXTRACT,
    METADATA_FILTER,
    RESOLVES_CONFLICTS,
    UPDATE,
    MemoryEvent,
    Message,
    Scope,
)
from llm_steward.providers.litemem import (
    HttpTransport,
    LiteEvent,
    LiteHit,
    LiteMemory,
    LiteMemProvider,
    LiteScope,
)

NS = uuid.UUID("01a1cc00-0000-7000-8000-000000000001")
MEM = "01a1cc00-0000-7000-8000-0000000000aa"
OLD = "01a1cc00-0000-7000-8000-0000000000bb"


class FakeTransport:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.events: list[LiteEvent] = [LiteEvent("ADD", MEM, "lives in Berlin", "extract:m")]
        self.memory: LiteMemory | None = LiteMemory(MEM, "lives in Berlin", LiteScope("ada"))
        self.down = False

    async def add(self, *args: Any) -> list[LiteEvent]:
        self.calls.append(("add", args))
        return self.events

    async def remember(self, *args: Any) -> list[LiteEvent]:
        self.calls.append(("remember", args))
        return self.events

    async def search(self, *args: Any) -> list[LiteHit]:
        self.calls.append(("search", args))
        return [LiteHit(MEM, 0.9, "lives in Berlin")]

    async def get(self, *args: Any) -> LiteMemory | None:
        self.calls.append(("get", args))
        return self.memory

    async def update(self, *args: Any) -> None:
        self.calls.append(("update", args))

    async def delete(self, *args: Any) -> None:
        self.calls.append(("delete", args))

    async def list(self, *args: Any) -> list[LiteMemory]:
        self.calls.append(("list", args))
        return [LiteMemory(MEM, "lives in Berlin", LiteScope("ada"), {"steward_id": "s-1"})]

    async def health(self) -> None:
        if self.down:
            raise ProviderUnavailable("lite-mem: embedding failed")

    async def aclose(self) -> None:
        self.calls.append(("aclose", ()))


def _provider() -> tuple[LiteMemProvider, FakeTransport]:
    transport = FakeTransport()
    return LiteMemProvider(transport, namespace_prefix="steward"), transport


async def test_an_inferred_add_names_the_namespace_scope_and_key() -> None:
    provider, transport = _provider()
    scope = Scope(NS, user_id="ada", session_id="s1")
    events = await provider.add(
        [Message("user", "I live in Berlin")], scope, idempotency_key="ep-9"
    )
    assert events == [MemoryEvent("ADD", f"{NS}/{MEM}", "lives in Berlin", "extract:m")]
    [(kind, (namespace, messages, lite_scope, metadata, key))] = transport.calls
    assert (kind, namespace, key) == ("add", f"steward/{NS}", "ep-9")
    assert lite_scope == LiteScope("ada", None, "s1") and metadata is None
    assert messages == [Message("user", "I live in Berlin")]


async def test_the_engines_decisions_become_replacements_and_retirements() -> None:
    provider, transport = _provider()
    transport.events = [
        LiteEvent("SUPERSEDE", MEM, "lives in Auckland", "resolve:m", OLD),
        LiteEvent("DELETE", OLD, "lives in Wellington", "resolve:m"),
        LiteEvent("ADD", MEM, "works as a nurse", "resolve:m"),  # a compound fact's remainder
        LiteEvent("EXPIRE", OLD, "old", None),  # not from llm-steward's calls: ignored
    ]
    events = await provider.add([Message("user", "moved")], Scope(NS, user_id="ada"))
    assert events == [
        MemoryEvent("ADD", f"{NS}/{MEM}", "lives in Auckland", "resolve:m", f"{NS}/{OLD}"),
        MemoryEvent("DELETE", f"{NS}/{OLD}", "lives in Wellington", "resolve:m"),
        MemoryEvent("ADD", f"{NS}/{MEM}", "works as a nurse", "resolve:m"),
    ]


async def test_a_governed_memory_is_remembered_with_the_callers_key() -> None:
    provider, transport = _provider()
    meta = {"steward_id": "0199-abc"}
    [event] = await provider.add(
        [Message("user", "likes tea")], Scope(NS, user_id="ada"), infer=False, metadata=meta,
        idempotency_key="0199-abc",
    )  # fmt: skip
    assert event.external_id == f"{NS}/{MEM}"
    [(kind, (namespace, content, _, metadata, key))] = transport.calls
    assert (kind, namespace, content, metadata, key) == (
        "remember", f"steward/{NS}", "likes tea", meta, "0199-abc"
    )  # fmt: skip


async def test_by_id_calls_find_their_namespace_in_the_id() -> None:
    provider, transport = _provider()
    record = await provider.get(f"{NS}/{MEM}")
    assert record is not None and record.scope == Scope(NS, user_id="ada")
    await provider.update(f"{NS}/{MEM}", "lives in Paris")
    await provider.delete(f"{NS}/{MEM}")
    assert [(k, a[:2]) for k, a in transport.calls] == [
        ("get", (f"steward/{NS}", MEM)),
        ("update", (f"steward/{NS}", MEM)),
        ("delete", (f"steward/{NS}", MEM)),
    ]
    transport.memory = None
    assert await provider.get(f"{NS}/{MEM}") is None
    # retired or superseded in lite-mem (kept for its history): gone, as the contract says
    transport.memory = LiteMemory(MEM, "lives in Berlin", LiteScope("ada"), current=False)
    assert await provider.get(f"{NS}/{MEM}") is None
    with pytest.raises(ValueError, match="not a lite-mem memory id"):
        await provider.delete("not-an-id")


async def test_search_and_list_map_back_to_external_ids() -> None:
    provider, _ = _provider()
    [hit] = await provider.search("Berlin", Scope(NS, user_id="ada"), top_k=5)
    assert (hit.external_id, hit.text) == (f"{NS}/{MEM}", "lives in Berlin")
    [record] = await provider.list(Scope(NS, user_id="ada"))
    assert (record.external_id, record.metadata) == (f"{NS}/{MEM}", {"steward_id": "s-1"})


async def test_capabilities_and_health() -> None:
    provider, transport = _provider()
    assert provider.capabilities == {EXTRACT, RESOLVES_CONFLICTS, UPDATE, DELETE, METADATA_FILTER}
    assert (await provider.health()).status == "ok"
    transport.down = True
    health = await provider.health()
    assert (health.status, health.detail) == ("error", "lite-mem: embedding failed")


def _http(handler: Any) -> HttpTransport:
    client = httpx2.AsyncClient(transport=httpx2.MockTransport(handler))
    return HttpTransport(client, "http://lite-mem.test", "lm_steward_x", 5.0)


async def test_the_http_transport_sends_the_namespace_key_and_idempotency_key() -> None:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json={"events": [
            {"op": "SUPERSEDE", "memory_id": MEM, "content": "x", "produced_by": "r:m",
             "replaced": OLD},
        ]})  # fmt: skip

    events = await _http(handler).add(f"steward/{NS}", [Message("user", "x")], LiteScope("ada"),
                                      None, "ep-1")  # fmt: skip
    assert events == [LiteEvent("SUPERSEDE", MEM, "x", "r:m", OLD)]
    [request] = seen
    assert request.headers["X-LiteMem-Namespace"] == f"steward/{NS}"
    assert request.headers["Authorization"] == "Bearer lm_steward_x"
    assert request.headers["Idempotency-Key"] == "ep-1"


@pytest.mark.parametrize(
    ("status", "error"),
    [
        (400, Unprocessable),
        (409, Unprocessable),
        (401, ProviderUnavailable),
        (503, ProviderUnavailable),
    ],
)
async def test_the_http_transport_maps_lite_mem_errors(status: int, error: type[Exception]) -> None:
    transport = _http(lambda r: httpx2.Response(status, json={"title": "t", "detail": "d"}))
    with pytest.raises(error):
        await transport.update(f"steward/{NS}", MEM, "x")


async def test_the_http_transport_treats_gone_as_done() -> None:
    for status in (404, 409):
        transport = _http(lambda r, s=status: httpx2.Response(s, json={}))
        await transport.delete(f"steward/{NS}", MEM)  # no error
    assert await _http(lambda r: httpx2.Response(404, json={})).get(f"steward/{NS}", MEM) is None


async def test_the_http_list_pages_until_a_short_page() -> None:
    from llm_steward.providers.litemem import LIST_PAGE

    offsets: list[str] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        offsets.append(request.url.params["offset"])
        size = LIST_PAGE if len(offsets) == 1 else 3
        memories = [{"id": str(uuid.uuid4()), "content": "m", "scope": {"user_id": "ada"}}] * size
        return httpx2.Response(200, json={"memories": memories})

    found = await _http(handler).list(f"steward/{NS}", LiteScope("ada"))
    assert len(found) == LIST_PAGE + 3 and offsets == ["0", str(LIST_PAGE)]
