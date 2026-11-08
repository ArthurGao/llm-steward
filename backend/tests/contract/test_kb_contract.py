"""The KnowledgeProvider contract (design §8): one lifecycle every KB adapter must pass —
ensure → upsert → hit → update → the new text → remove → no hit → health.

`native` is the reference and runs in the default suite on both dialects. lite-rag runs under
`-m contract`, really: a lite-rag runtime on the local PostgreSQL (its own schema `literag_test`,
created and dropped here) with lite-rag's fake embedder (no Ollama), in-process and behind its real
HTTP app (`create_app`, API keys on, reached through ASGI).
"""

import uuid
from collections.abc import AsyncIterator, Iterator
from typing import Any

import httpx2
import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_steward.errors import ProviderUnavailable
from llm_steward.providers.base import KbCollectionRef, KbEntryDoc, KnowledgeProvider
from llm_steward.providers.literag import HttpTransport, InProcessTransport, LiteRagProvider
from llm_steward.providers.native.providers import NativeKnowledgeProvider, create_store
from llm_steward.providers.weknora import WeKnoraProvider
from llm_steward.storage.db import engine_scope, prepare_schema
from tests.conftest import TEST_SCHEMA, _pg_url
from tests.fakes.embedder import FakeEmbedder

NS = uuid.UUID("00000000-0000-7000-8000-000000000001")
LITERAG_SCHEMA = "literag_test"


async def check_lifecycle(kb: KnowledgeProvider) -> str:
    """The contract; returns the first entry's external id (adapters differ in its form)."""
    refunds = await kb.ensure_collection(KbCollectionRef(uuid.uuid4(), NS, "refunds"))
    shipping = await kb.ensure_collection(KbCollectionRef(uuid.uuid4(), NS, "shipping"))
    entry = KbEntryDoc(uuid.uuid4(), NS, "Refund time", "Refunds take 3-5 business days", 1)
    other = KbEntryDoc(uuid.uuid4(), NS, "Courier", "Parcels ship by courier", 1)

    eid = await kb.upsert_entry(entry, refunds)
    other_id = await kb.upsert_entry(other, shipping)

    hits = await kb.search("how long do refunds take", [refunds], top_k=5)
    assert [h.external_id for h in hits] == [eid]
    assert "Refunds take" in hits[0].text
    # Only the collections asked for are searched. (Vector search always returns the nearest
    # items, so the shipping entry comes back for any query; the refunds entry must not.)
    in_shipping = await kb.search("refunds", [shipping], top_k=5)
    assert [h.external_id for h in in_shipping] == [other_id]

    again = await kb.upsert_entry(  # as the Syncer calls it: with the id it was synced under
        KbEntryDoc(entry.entry_id, NS, "Refund time", "Refunds now take 7 days", 2), refunds, eid
    )
    assert again == eid  # the same entry, replaced
    [updated] = await kb.search("refunds", [refunds], top_k=5)
    assert "7 days" in updated.text

    await kb.remove(eid)
    assert await kb.search("refunds", [refunds], top_k=5) == []
    never_synced = await kb.ensure_collection(KbCollectionRef(uuid.uuid4(), NS, "empty"))
    assert await kb.search("refunds", [never_synced], top_k=5) == []  # not an error
    await kb.remove(eid)  # idempotent: the Syncer retries
    assert (await kb.health()).status == "ok"
    return eid


# --- native (reference) -----------------------------------------------------------------------


@pytest.fixture
async def engine(db_url: str) -> AsyncIterator[AsyncEngine]:
    async with engine_scope(db_url, schema=TEST_SCHEMA) as e:
        await prepare_schema(e, TEST_SCHEMA)
        yield e


async def test_native(engine: AsyncEngine) -> None:
    embedder = FakeEmbedder()
    kb = NativeKnowledgeProvider(create_store(engine, embedder, TEST_SCHEMA), embedder)
    eid = await check_lifecycle(kb)
    uuid.UUID(eid)  # native: the entry id itself


# --- lite-rag (real engine, -m contract) ----------------------------------------------------


@pytest.fixture
def literag_runtime() -> Iterator[Any]:
    import psycopg
    from lite_rag.config import load_settings
    from lite_rag.db import migrate
    from lite_rag.embedding import CachingEmbedder
    from lite_rag.engine import RagRuntime
    from lite_rag.stores.pg import PgStore
    from lite_rag.testing import FakeEmbedder as LiteFakeEmbedder

    url = make_url(_pg_url()).set(drivername="postgresql").render_as_string(hide_password=False)
    drop = f'DROP SCHEMA IF EXISTS "{LITERAG_SCHEMA}" CASCADE'
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(drop)
        migrate(conn, LITERAG_SCHEMA)
    settings = load_settings(
        store={"schema": LITERAG_SCHEMA},
        chunking={"strategy": "paragraph", "unit": "chars", "size": 400, "overlap": 0},
    )
    store = PgStore(url, LITERAG_SCHEMA)
    runtime = RagRuntime(settings, store, CachingEmbedder(LiteFakeEmbedder(dim=16), store))
    yield runtime
    runtime.close()
    with psycopg.connect(url, autocommit=True) as conn:
        conn.execute(drop)


def _app_client(runtime: Any, namespaces: list[str]) -> tuple[httpx2.AsyncClient, str]:
    """lite-rag's real HTTP app with one write key, reached in-process through ASGI."""
    from lite_rag.config import ApiKeyConfig
    from lite_rag.server import create_app
    from lite_rag.server.auth import new_key

    token, digest = new_key()
    key = ApiKeyConfig(name="steward", sha256=digest, namespaces=namespaces, mode="write")
    settings = runtime.settings.model_copy(
        update={"server": runtime.settings.server.model_copy(update={"keys": [key]})}
    )
    app = create_app(settings, runtime)
    return httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app)), token


def _http_provider(http: httpx2.AsyncClient, token: str | None) -> LiteRagProvider:
    return LiteRagProvider(HttpTransport(http, "http://literag", token, timeout_s=10))


@pytest.mark.contract
async def test_literag_in_process(literag_runtime: Any) -> None:
    eid = await check_lifecycle(LiteRagProvider(InProcessTransport(literag_runtime, concurrency=4)))
    assert eid.startswith(f"{NS}/") and "#" in eid  # collection and entry, both in the id


@pytest.mark.contract
async def test_literag_over_http(literag_runtime: Any) -> None:
    http, token = _app_client(literag_runtime, ["steward/*"])
    async with http:
        await check_lifecycle(_http_provider(http, token))


@pytest.mark.contract
async def test_literag_over_http_refuses_a_key_for_other_namespaces(literag_runtime: Any) -> None:
    http, token = _app_client(literag_runtime, ["someone-else/*"])
    async with http:
        kb = _http_provider(http, token)
        collection = await kb.ensure_collection(KbCollectionRef(uuid.uuid4(), NS, "c"))
        entry = KbEntryDoc(uuid.uuid4(), NS, "T", "text", 1)
        with pytest.raises(ProviderUnavailable, match="HTTP 403"):
            await kb.upsert_entry(entry, collection)
        no_key = _http_provider(http, None)
        with pytest.raises(ProviderUnavailable, match="HTTP 401"):
            await no_key.search("x", [collection], top_k=1)


@pytest.mark.contract
def test_literag_end_to_end_through_governance(migrated_url: str, literag_runtime: Any) -> None:
    """The whole path, real on both sides: an active entry is synced by the worker into lite-rag
    (over its HTTP API) and found by /v1/retrieve; once deprecated and synced, lite-rag itself no
    longer has it (not only the governance safety net)."""
    from fastapi.testclient import TestClient

    from llm_steward.main import create_app
    from llm_steward.models.enums import KbStatus
    from llm_steward.services.governance_service import CreateCollection, TransitionKbEntry
    from tests.conftest import AppClient, kb_entry_op, test_settings

    http, token = _app_client(literag_runtime, ["steward/*"])
    settings = test_settings(
        migrated_url, kb_provider="literag", literag_url="http://literag", literag_api_key=token
    )
    app = create_app(settings, http=http, embedder=FakeEmbedder())
    with TestClient(app) as client:
        c = AppClient(client, app.state.services)
        headers, ns_id = c.new_tenant("acme")
        collection = c.apply(CreateCollection(ns_id, "faq"))
        entry = c.apply(kb_entry_op(ns_id, collection.id, KbStatus.ACTIVE))
        assert c.drain() >= 1

        def retrieve() -> list[str]:
            body = {"query": "how long do refunds take", "sources": ["kb"]}
            response = client.post("/v1/retrieve", json=body, headers=headers)
            assert response.status_code == 200, response.text
            assert response.json()["degraded"] == []
            return [i["id"] for i in response.json()["items"]]

        assert retrieve() == [str(entry.id)]

        c.apply(TransitionKbEntry(ns_id, entry.id, KbStatus.DEPRECATED))
        assert c.drain() >= 1
        assert retrieve() == []
        in_literag = c.call(
            app.state.services.providers.kb.search, "refunds", [c.collection_ext_id(entry)], top_k=5
        )
        assert in_literag == []  # removed from lite-rag, not just hidden


# --- WeKnora v0.9.0 (real engine, -m contract) ----------------------------------------------


class _WeKnoraTarget(BaseSettings):
    """The local `standard` stack (deploy/weknora; key and model from deploy/weknora/bootstrap.py),
    read from the environment or backend/.env. Missing: the test fails, it never skips."""

    model_config = SettingsConfigDict(
        env_prefix="STEWARD_WEKNORA_", env_file=".env", extra="ignore"
    )
    url: str = "http://127.0.0.1:8680"
    api_key: str | None = None
    embedding_model_id: str | None = None


@pytest.fixture
def weknora_target() -> Iterator[_WeKnoraTarget]:
    """The configured local WeKnora; afterwards, every knowledge base the test created is deleted
    (listed before and after: governance never drops a collection, so no provider call does)."""
    target = _WeKnoraTarget()
    if not (target.api_key and target.embedding_model_id):
        pytest.fail(
            "WeKnora contract needs STEWARD_WEKNORA_API_KEY and STEWARD_WEKNORA_EMBEDDING_MODEL_ID "
            "(docker compose --profile standard up; python deploy/weknora/bootstrap.py)"
        )
    before = set(_weknora_kbs(target))
    yield target
    for kb_id in set(_weknora_kbs(target)) - before:
        httpx2.delete(f"{target.url}/api/v1/knowledge-bases/{kb_id}", headers=_auth(target))


@pytest.fixture
async def weknora(weknora_target: _WeKnoraTarget) -> AsyncIterator[WeKnoraProvider]:
    t = weknora_target
    async with httpx2.AsyncClient() as http:
        yield WeKnoraProvider(http, t.url, t.api_key, t.embedding_model_id, timeout_s=10)


@pytest.mark.contract
async def test_weknora(weknora: WeKnoraProvider) -> None:
    eid = await check_lifecycle(weknora)
    uuid.UUID(eid)  # WeKnora's own knowledge id


@pytest.mark.contract
def test_weknora_acceptance_through_governance(
    migrated_url: str, weknora_target: _WeKnoraTarget
) -> None:
    """P3 acceptance (design §11), real on both sides: a high-risk entry never reaches WeKnora
    before a reviewer approves it; a deprecated one leaves retrieval at once — while WeKnora still
    has it, with the worker stopped — and leaves WeKnora when the worker runs."""
    from fastapi.testclient import TestClient

    from llm_steward.main import create_app
    from llm_steward.models.enums import ApiScope
    from tests.conftest import AppClient, test_settings

    target = weknora_target
    settings = test_settings(
        migrated_url,
        kb_provider="weknora",
        weknora_url=target.url,
        weknora_api_key=target.api_key,
        weknora_embedding_model_id=target.embedding_model_id,
        provider_timeout_ms=10_000,
        retrieve_timeout_ms=10_000,
    )
    app = create_app(settings, embedder=FakeEmbedder())
    with TestClient(app) as client:
        c = AppClient(client, app.state.services)
        host, _ = c.new_tenant("acme", ApiScope.READ, ApiScope.WRITE)
        reviewer_key = c.call(
            c.services.namespaces.create_key,
            "acme",
            (ApiScope.READ, ApiScope.WRITE, ApiScope.REVIEW),
        )
        reviewer = {"Authorization": f"Bearer {reviewer_key}"}
        collection = client.post(
            "/v1/kb/collections", json={"name": "faq", "default_risk": "low"}, headers=reviewer
        ).json()["id"]
        body = {
            "collection_id": collection,
            "title": "Refund policy",
            "content": "Refunds take 3 to 5 business days.",
            "risk_level": "high",
        }
        card = client.post("/v1/kb/entries", json=body, headers=host).json()["candidate"]
        c.drain()
        assert _weknora_kb(target, collection) is None  # nothing reached WeKnora at all

        def retrieve() -> list[str]:
            r = client.post(
                "/v1/retrieve", json={"query": "refunds", "sources": ["kb"]}, headers=host
            )
            assert r.status_code == 200 and r.json()["degraded"] == [], r.text
            return [i["id"] for i in r.json()["items"]]

        approved = client.post(f"/v1/candidates/{card['id']}/accept", headers=reviewer).json()
        entry = approved["entry"]
        assert entry["status"] == "verified"
        c.drain()
        assert retrieve() == [entry["id"]]

        client.post(f"/v1/kb/entries/{entry['id']}/deprecate", headers=reviewer)
        assert retrieve() == []  # worker not run: the safety net
        kb = c.services.providers.kb
        ext = _weknora_kb(target, collection)  # looked up: ensure_collection would create one
        assert ext is not None
        assert c.call(kb.search, "refunds", [ext], top_k=5) != []  # WeKnora still has it
        c.drain()
        assert c.call(kb.search, "refunds", [ext], top_k=5) == []


def _auth(target: _WeKnoraTarget) -> dict[str, str]:
    return {"X-API-Key": target.api_key or ""}


def _weknora_kbs(target: _WeKnoraTarget) -> dict[str, str]:
    """name → id of every knowledge base in the WeKnora workspace."""
    r = httpx2.get(f"{target.url}/api/v1/knowledge-bases", headers=_auth(target))
    return {kb["id"]: kb["name"] for kb in r.json()["data"]}


def _weknora_kb(target: _WeKnoraTarget, collection_id: str) -> str | None:
    """The WeKnora knowledge base of a llm-steward collection, if it was ever created."""
    name = f"steward-{collection_id}"
    return next((i for i, n in _weknora_kbs(target).items() if n == name), None)
