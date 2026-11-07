"""Provider contract (design §8), run against the native providers on both dialects.

write → hit → update → delete → no hit → health. Later adapters (mem0, WeKnora, RAGFlow) get the
same suite against their real engines under `-m contract`.
"""

import uuid
from collections.abc import AsyncIterator

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_steward.errors import ProviderUnavailable, Unprocessable
from llm_steward.providers.base import KbCollectionRef, KbEntryDoc, Message, Scope
from llm_steward.providers.native.providers import (
    NativeKnowledgeProvider,
    NativeMemoryProvider,
    create_store,
)
from llm_steward.storage.db import engine_scope, prepare_schema
from tests.conftest import TEST_SCHEMA
from tests.fakes.embedder import FakeEmbedder

NS = uuid.UUID("00000000-0000-7000-8000-000000000001")
OTHER_NS = uuid.UUID("00000000-0000-7000-8000-000000000002")


@pytest.fixture
async def engine(db_url: str) -> AsyncIterator[AsyncEngine]:
    async with engine_scope(db_url, schema=TEST_SCHEMA) as e:
        await prepare_schema(e, TEST_SCHEMA)
        yield e


@pytest.fixture
def memory(engine: AsyncEngine) -> NativeMemoryProvider:
    embedder = FakeEmbedder()
    return NativeMemoryProvider(create_store(engine, embedder, TEST_SCHEMA), embedder)


@pytest.fixture
def kb(engine: AsyncEngine) -> NativeKnowledgeProvider:
    embedder = FakeEmbedder()
    return NativeKnowledgeProvider(create_store(engine, embedder, TEST_SCHEMA), embedder)


async def _add(provider: NativeMemoryProvider, text: str, scope: Scope, steward_id: str) -> str:
    [event] = await provider.add(
        [Message("user", text)], scope, infer=False, metadata={"steward_id": steward_id}
    )
    return event.external_id


async def test_memory_lifecycle(memory: NativeMemoryProvider) -> None:
    scope = Scope(NS, user_id="u1")
    eid = await _add(memory, "user lives in Beijing", scope, "m-1")
    assert eid == "m-1"

    hits = await memory.search("where does the user live Beijing", scope, top_k=5)
    assert [h.external_id for h in hits] == ["m-1"]

    [again] = await memory.add(
        [Message("user", "user lives in Beijing")],
        scope,
        infer=False,
        metadata={"steward_id": "m-1"},
    )
    assert (again.op, again.external_id) == ("UPDATE", "m-1")  # idempotent by steward id

    await memory.update("m-1", "user moved to Shanghai")
    record = await memory.get("m-1")
    assert record is not None and record.content == "user moved to Shanghai"
    assert record.scope == scope
    assert [h.external_id for h in await memory.search("Shanghai", scope, top_k=5)] == ["m-1"]

    await memory.delete("m-1")
    assert await memory.get("m-1") is None
    assert await memory.search("Shanghai", scope, top_k=5) == []
    assert (await memory.health()).status == "ok"


async def test_memory_search_honours_scope_and_namespace(memory: NativeMemoryProvider) -> None:
    await _add(memory, "likes green tea", Scope(NS, user_id="alice"), "alice-1")
    await _add(memory, "likes green tea", Scope(NS, user_id="bob"), "bob-1")
    await _add(memory, "office serves green tea", Scope(NS), "shared-1")
    await _add(memory, "likes green tea", Scope(OTHER_NS, user_id="alice"), "other-ns-1")

    found = {h.external_id for h in await memory.search("green tea", Scope(NS, "alice"), top_k=10)}
    assert found == {"alice-1", "shared-1"}  # own + shared; never bob's, never another namespace's
    listed = {r.external_id for r in await memory.list(Scope(NS, user_id="bob"))}
    assert listed == {"bob-1", "shared-1"}


async def test_memory_without_extract_rejects_infer(memory: NativeMemoryProvider) -> None:
    with pytest.raises(Unprocessable):
        await memory.add([Message("user", "x")], Scope(NS), infer=True)


async def test_knowledge_lifecycle(kb: NativeKnowledgeProvider) -> None:
    refunds = await kb.ensure_collection(KbCollectionRef(uuid.uuid4(), NS, "refunds"))
    shipping = await kb.ensure_collection(KbCollectionRef(uuid.uuid4(), NS, "shipping"))
    entry = KbEntryDoc(uuid.uuid4(), NS, "Refund time", "Refunds take 3-5 business days", 1)
    other = KbEntryDoc(uuid.uuid4(), NS, "Courier", "Parcels ship by courier", 1)

    eid = await kb.upsert_entry(entry, refunds)
    await kb.upsert_entry(other, shipping)
    assert eid == str(entry.entry_id)

    hits = await kb.search("how long do refunds take", [refunds], top_k=5)
    assert [h.external_id for h in hits] == [eid]
    assert "Refunds take" in hits[0].text
    # Only the collections asked for are searched. (KNN always returns the nearest items, so the
    # shipping entry comes back for any query; the refunds entry must not.)
    in_shipping = await kb.search("refunds", [shipping], top_k=5)
    assert [h.external_id for h in in_shipping] == [str(other.entry_id)]

    await kb.upsert_entry(
        KbEntryDoc(entry.entry_id, NS, "Refund time", "Refunds now take 7 days", 2), refunds
    )
    [updated] = await kb.search("refunds", [refunds], top_k=5)
    assert "7 days" in updated.text

    await kb.remove(eid)
    assert await kb.search("refunds", [refunds], top_k=5) == []


async def test_a_different_embedding_model_is_refused(engine: AsyncEngine) -> None:
    first = FakeEmbedder()
    provider = NativeMemoryProvider(create_store(engine, first, TEST_SCHEMA), first)
    await _add(provider, "something", Scope(NS), "x-1")

    class OtherModel(FakeEmbedder):
        model = "another-model"

    other = OtherModel()
    switched = NativeMemoryProvider(create_store(engine, other, TEST_SCHEMA), other)
    with pytest.raises(ProviderUnavailable, match="rebuild the native index"):
        await switched.search("something", Scope(NS), top_k=3)


async def test_pg_refuses_to_create_tables_outside_the_schema(pg_url: str) -> None:
    """Without the governance schema, PostgreSQL would fall through the search_path into public."""
    native_in_public = (
        "SELECT tablename FROM pg_tables WHERE schemaname = 'public' AND tablename LIKE 'native%'"
    )
    async with engine_scope(pg_url, schema=TEST_SCHEMA) as engine:  # schema not created
        async with engine.connect() as conn:
            before = set(await conn.scalars(text(native_in_public)))
        embedder = FakeEmbedder()
        provider = NativeMemoryProvider(create_store(engine, embedder, TEST_SCHEMA), embedder)
        try:
            with pytest.raises(ProviderUnavailable, match="steward migrate"):
                await provider.search("x", Scope(NS), top_k=1)
        finally:
            # If the guard ever regresses, do not leave tables behind in the shared database.
            async with engine.begin() as conn:
                leaked = set(await conn.scalars(text(native_in_public))) - before
                for table in leaked:
                    await conn.execute(text(f'DROP TABLE public."{table}"'))
        assert not leaked, f"native tables were created in public: {sorted(leaked)}"


async def test_health_reports_an_unusable_index(engine: AsyncEngine) -> None:
    """/health must not claim ok while every search would fail."""
    first = FakeEmbedder()
    await NativeMemoryProvider(create_store(engine, first, TEST_SCHEMA), first).health()

    class OtherModel(FakeEmbedder):
        model = "another-model"

    other = OtherModel()
    with pytest.raises(ProviderUnavailable, match="rebuild the native index"):
        await NativeMemoryProvider(create_store(engine, other, TEST_SCHEMA), other).health()


class _FixedEmbedder:
    """Puts the query next to COFFEE in vector space, whatever the words say."""

    model = "fixed"
    dim = 384

    def __init__(self, vectors: dict[str, list[float]]) -> None:
        self._vectors = vectors

    async def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._vectors[t] for t in texts]


def _axis(i: int, lean: float = 0.0, j: int = 0) -> list[float]:
    vector = [0.0] * 384
    vector[i] = 1.0
    vector[j] += lean
    return vector


async def test_a_generic_word_cannot_outrank_the_vector_winner(engine: AsyncEngine) -> None:
    """Through the whole hybrid path: the query shares only '用户' with CITY, while the vector
    space puts it right next to COFFEE (found with the real model; see test_real_model.py)."""
    coffee, city, query = "The user drinks black coffee", "用户住在上海", "用户早上喝什么"
    embedder = _FixedEmbedder({coffee: _axis(0), city: _axis(1), query: _axis(0, 0.3, 1)})
    provider = NativeMemoryProvider(create_store(engine, embedder, TEST_SCHEMA), embedder)
    await _add(provider, coffee, Scope(NS), "coffee")
    await _add(provider, city, Scope(NS), "city")
    hits = await provider.search(query, Scope(NS), top_k=2)
    assert [h.external_id for h in hits] == ["coffee", "city"]
