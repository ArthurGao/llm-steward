"""LiteRagProvider's mapping onto lite-rag (design §8), over a fake transport: ids, best hit per
entry, merging collections, missing kbs, errors. The real lite-rag runs in tests/contract."""

import asyncio
import threading
import time
import uuid
from typing import Any

import pytest

from llm_steward.errors import ProviderUnavailable, Unprocessable
from llm_steward.providers.base import ENTRY_UPSERT, METADATA_FILTER, KbCollectionRef, KbEntryDoc
from llm_steward.providers.literag import InProcessTransport, LiteHit, LiteRagProvider

NS = uuid.UUID("00000000-0000-7000-8000-000000000001")
REFUNDS = uuid.UUID("00000000-0000-7000-8000-0000000000a1")
SHIPPING = uuid.UUID("00000000-0000-7000-8000-0000000000a2")


class FakeTransport:
    """lite-rag in memory: {(namespace, kb): {doc_id: (title, text, metadata)}}; search returns
    the canned hits per kb."""

    def __init__(self) -> None:
        self.docs: dict[tuple[str, str], dict[str, tuple[str, str, dict[str, Any]]]] = {}
        self.hits: dict[tuple[str, str], list[LiteHit]] = {}
        self.searched: list[tuple[str, list[str], str, int, dict[str, Any] | None]] = []
        self.down = False
        self.closed = False

    async def upsert(
        self, namespace: str, kb: str, doc_id: str, title: str, text: str, metadata: dict[str, Any]
    ) -> None:
        self.docs.setdefault((namespace, kb), {})[doc_id] = (title, text, metadata)

    async def delete(self, namespace: str, kb: str, doc_id: str) -> None:
        self.docs.get((namespace, kb), {}).pop(doc_id, None)

    async def search(
        self, namespace: str, kbs: list[str], query: str, top_k: int, where: dict[str, Any] | None
    ) -> list[LiteHit]:
        self.searched.append((namespace, list(kbs), query, top_k, where))
        return [h for kb in kbs for h in self.hits.get((namespace, kb), [])]

    async def health(self) -> None:
        if self.down:
            raise ProviderUnavailable("lite-rag GET /health: ConnectError")

    async def aclose(self) -> None:
        self.closed = True


@pytest.fixture
def transport() -> FakeTransport:
    return FakeTransport()


@pytest.fixture
def provider(transport: FakeTransport) -> LiteRagProvider:
    return LiteRagProvider(transport, namespace_prefix="steward")


def _entry(content: str = "Refunds take 3-5 days", version: int = 1) -> KbEntryDoc:
    return KbEntryDoc(uuid.uuid4(), NS, "Refund time", content, version, tags=["money"])


async def test_a_collection_becomes_a_kb_in_the_namespace_of_its_owner(
    provider: LiteRagProvider, transport: FakeTransport
) -> None:
    ext = await provider.ensure_collection(KbCollectionRef(REFUNDS, NS, "Refunds & Returns"))
    entry = _entry()
    await provider.upsert_entry(entry, ext)

    key = (f"steward/{NS}", f"c-{REFUNDS}")  # the name is free text; the id is a valid kb name
    assert transport.docs[key] == {
        str(entry.entry_id): (
            "Refund time",
            "Refunds take 3-5 days",
            {"steward_id": str(entry.entry_id), "version": 1, "tags": ["money"]},
        )
    }


async def test_an_external_id_finds_its_way_back_to_the_document(
    provider: LiteRagProvider, transport: FakeTransport
) -> None:
    ext = await provider.ensure_collection(KbCollectionRef(REFUNDS, NS, "refunds"))
    entry = _entry()
    external_id = await provider.upsert_entry(entry, ext)

    await provider.remove(external_id)  # no collection given: the id carries it
    assert transport.docs[(f"steward/{NS}", f"c-{REFUNDS}")] == {}
    await provider.remove(external_id)  # idempotent


async def test_one_search_covers_every_collection_and_keeps_each_entrys_best_hit(
    provider: LiteRagProvider, transport: FakeTransport
) -> None:
    refunds = await provider.ensure_collection(KbCollectionRef(REFUNDS, NS, "refunds"))
    shipping = await provider.ensure_collection(KbCollectionRef(SHIPPING, NS, "shipping"))
    ns = f"steward/{NS}"
    transport.hits[(ns, f"c-{REFUNDS}")] = [
        LiteHit(f"c-{REFUNDS}", "e1", 0.9, "refund chunk 1"),
        LiteHit(f"c-{REFUNDS}", "e1", 0.4, "refund chunk 2"),  # same entry, a weaker chunk
        LiteHit(f"c-{REFUNDS}", "e2", 0.2, "other refund"),
    ]
    transport.hits[(ns, f"c-{SHIPPING}")] = [LiteHit(f"c-{SHIPPING}", "e3", 0.5, "shipping")]

    hits = await provider.search("q", [refunds, shipping], top_k=2, filters={"version": 1})

    assert [(h.external_id, h.score, h.text) for h in hits] == [
        (f"{refunds}#e1", 0.9, "refund chunk 1"),
        (f"{shipping}#e3", 0.5, "shipping"),
    ]
    # one lite-rag search over both kbs (one query embedding, one fusion), not one per kb
    assert transport.searched == [(ns, [f"c-{REFUNDS}", f"c-{SHIPPING}"], "q", 2, {"version": 1})]
    assert await provider.search("q", [], top_k=5) == []


async def test_capabilities_and_health(provider: LiteRagProvider, transport: FakeTransport) -> None:
    assert provider.name == "literag"
    assert provider.capabilities == frozenset({ENTRY_UPSERT, METADATA_FILTER})
    assert (await provider.health()).status == "ok"
    transport.down = True
    health = await provider.health()
    assert health.status == "error" and "ConnectError" in (health.detail or "")
    await provider.aclose()
    assert transport.closed


@pytest.mark.parametrize("bad", ["no-slash", f"{NS}/not-a-uuid", f"{NS}/{REFUNDS}/x"])
async def test_a_malformed_collection_id_is_refused(provider: LiteRagProvider, bad: str) -> None:
    with pytest.raises(ValueError, match="lite-rag collection id"):  # our invariant, not an outage
        await provider.upsert_entry(_entry(), bad)


# --- in-process transport, over a fake lite-rag runtime ----------------------------------------


class _FakeEngine:
    def __init__(self, runtime: "_FakeRuntime") -> None:
        self._runtime = runtime

    def search_kbs(self, kbs: list[str], query: str, **kwargs: Any) -> list[Any]:
        from lite_rag.errors import InvalidRequest, KbNotFound, StoreError

        [kb] = kbs
        rt = self._runtime
        with rt.lock:
            rt.running += 1
            rt.peak = max(rt.peak, rt.running)
        time.sleep(0.02)
        with rt.lock:
            rt.running -= 1
        if kb == "missing":
            raise KbNotFound("no such kb")
        if kb == "bad":
            raise InvalidRequest("metadata filter 'x': values must be scalars")
        if kb == "down":
            raise StoreError("cannot connect to postgresql://u:secret@db")
        return []


class _FakeRuntime:
    def __init__(self) -> None:
        self.engines: list[str] = []
        self.lock = threading.Lock()
        self.running = self.peak = 0

    def engine(self, namespace: str) -> _FakeEngine:
        self.engines.append(namespace)
        return _FakeEngine(self)


async def test_in_process_calls_are_bounded_and_reuse_one_engine_per_namespace() -> None:
    runtime = _FakeRuntime()
    transport = InProcessTransport(runtime, concurrency=2)  # type: ignore[arg-type]
    await asyncio.gather(*(transport.search("steward/a", ["kb"], "q", 5, None) for _ in range(6)))
    await transport.search("steward/b", ["kb"], "q", 5, None)
    assert runtime.peak == 2
    assert runtime.engines == ["steward/a", "steward/b"]  # kept: it remembers checked kbs


async def test_in_process_errors_become_ours(caplog: pytest.LogCaptureFixture) -> None:
    transport = InProcessTransport(_FakeRuntime(), concurrency=1)  # type: ignore[arg-type]
    assert await transport.search("ns", ["missing"], "q", 5, None) == []  # nothing synced yet
    with pytest.raises(Unprocessable, match="scalars"):
        await transport.search("ns", ["bad"], "q", 5, None)
    with pytest.raises(ProviderUnavailable) as caught:
        await transport.search("ns", ["down"], "q", 5, None)
    assert "secret" not in str(caught.value)  # lite-rag's message goes to the log only
    logged = " ".join(r.getMessage() for r in caplog.records)
    assert "cannot connect" in logged and "secret" not in logged  # and without credentials
