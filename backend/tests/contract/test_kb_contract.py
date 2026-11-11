"""The KnowledgeProvider contract (design §8): one lifecycle every KB adapter must pass —
ensure → upsert → hit → update → the new text → remove → no hit → health — and, for adapters with
`doc_parsing`, the document lifecycle: upload → parsed → its chunks hit → the same file again is the
same document → remove → no hit, status failed.

`native` is the reference and runs in the default suite on both dialects. lite-rag runs under
`-m contract`, really: a lite-rag runtime on the local PostgreSQL (its own schema `literag_test`,
created and dropped here) with lite-rag's fake embedder (no Ollama), in-process and behind its real
HTTP app (`create_app`, API keys on, reached through ASGI).
"""

import asyncio
import time
import uuid
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, cast

import httpx2
import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine

from llm_steward.errors import ProviderUnavailable
from llm_steward.models.enums import DocStatus
from llm_steward.providers.base import (
    KbCollectionRef,
    KbDocumentUpload,
    KbEntryDoc,
    KnowledgeProvider,
    collection_name,
    document_parser,
)
from llm_steward.providers.literag import HttpTransport, InProcessTransport, LiteRagProvider
from llm_steward.providers.native.providers import NativeKnowledgeProvider, create_store
from llm_steward.providers.ragflow import RAGFlowProvider
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


POLICY = (
    b"# Customer policy\n\nRefunds take 3 to 5 business days after the parcel arrives.\n\n"
    b"Shipping is free above 50 euros.\n"
)


async def check_documents(kb: KnowledgeProvider, *, settle_s: float = 0) -> str:
    """The document contract (`doc_parsing`); returns the document's external id. `settle_s`: how
    long the engine may take to parse it (polled, as the Syncer does)."""
    parser = document_parser(kb)
    assert parser is not None, f"{kb.name} lacks doc_parsing"
    docs = await kb.ensure_collection(KbCollectionRef(uuid.uuid4(), NS, "docs"))
    upload = KbDocumentUpload(
        uuid.uuid4(), NS, "Policy", f"policy-{uuid.uuid4().hex}.md", None, POLICY
    )

    did = await parser.ingest_document(upload, docs)
    deadline = time.monotonic() + settle_s
    status = await parser.document_status(did)
    while status in (DocStatus.PENDING, DocStatus.PROCESSING) and time.monotonic() < deadline:
        await asyncio.sleep(0.5)
        status = await parser.document_status(did)
    assert status == DocStatus.READY

    [hit] = await kb.search("how long do refunds take", [docs], top_k=5)
    assert hit.external_id == did  # its chunks answer for the document: one hit
    assert "Refunds take 3 to 5 business days" in hit.text  # the best chunk
    assert hit.passages[0] == hit.text
    # a query touching both sections finds both, as that hit's passages (the engine sizes
    # chunks: one chunk may hold both)
    [both] = await kb.search("refunds business days and free shipping euros", [docs], top_k=5)
    found = " ".join(both.passages)
    assert "Refunds take" in found and "Shipping is free" in found
    assert await parser.ingest_document(upload, docs) == did  # a retried upload: no second copy

    await kb.remove(did)
    assert await kb.search("refunds", [docs], top_k=5) == []
    assert await parser.document_status(did) == DocStatus.FAILED  # gone
    await kb.remove(did)  # idempotent
    return did


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
    assert (await check_documents(kb)).startswith("doc:")


async def test_native_fails_what_it_cannot_parse(engine: AsyncEngine) -> None:
    embedder = FakeEmbedder()
    kb = NativeKnowledgeProvider(create_store(engine, embedder, TEST_SCHEMA), embedder)
    docs = await kb.ensure_collection(KbCollectionRef(uuid.uuid4(), NS, "docs"))
    for name, data in (("scan.pdf", b"%PDF-1.7"), ("latin.txt", "caf\xe9".encode("latin-1"))):
        did = await kb.ingest_document(
            KbDocumentUpload(uuid.uuid4(), NS, "x", name, None, data), docs
        )
        assert await kb.document_status(did) == DocStatus.FAILED


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
    uuid.UUID(await check_documents(weknora, settle_s=60))


@dataclass(frozen=True)
class _Engine:
    """A real KB engine for the governance-level tests: the Settings that point the app at it,
    and how to look up a llm-steward collection's id there (None: never created)."""

    settings: dict[str, Any]
    collection: Callable[[str], str | None]


@pytest.fixture
def weknora_engine(weknora_target: _WeKnoraTarget) -> _Engine:
    t = weknora_target
    settings = {
        "kb_provider": "weknora",
        "weknora_url": t.url,
        "weknora_api_key": t.api_key,
        "weknora_embedding_model_id": t.embedding_model_id,
    }
    return _Engine(settings, lambda collection: _named(_weknora_kbs(t), collection))


@pytest.fixture(params=["weknora", "ragflow"])
def real_engine(request: pytest.FixtureRequest) -> _Engine:
    return cast(_Engine, request.getfixturevalue(f"{request.param}_engine"))


@pytest.mark.contract
def test_acceptance_through_governance(migrated_url: str, real_engine: _Engine) -> None:
    """P3 acceptance (design §11), real on both sides: a high-risk entry never reaches the engine
    before a reviewer approves it; a deprecated one leaves retrieval at once — while the engine
    still has it, with the worker stopped — and leaves the engine when the worker runs."""
    with _engine_app(migrated_url, real_engine) as (c, host, reviewer):
        client = c.http
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
        assert real_engine.collection(collection) is None  # nothing reached the engine at all

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
        ext = real_engine.collection(collection)  # looked up: ensure_collection would create one
        assert ext is not None
        assert c.call(kb.search, "refunds", [ext], top_k=5) != []  # the engine still has it
        c.drain()
        assert c.call(kb.search, "refunds", [ext], top_k=5) == []


@pytest.mark.contract
def test_documents_through_governance(migrated_url: str, real_engine: _Engine) -> None:
    """P3d, real on both sides: a document in a high-risk collection waits for a reviewer with
    nothing in the engine; approved, the worker uploads it and polls until the engine has parsed
    it, and retrieval serves its chunk; deleted, it leaves retrieval at once and the engine when
    the worker runs."""
    with _engine_app(migrated_url, real_engine) as (c, host, reviewer):
        collection = c.http.post(
            "/v1/kb/collections", json={"name": "docs", "default_risk": "high"}, headers=reviewer
        ).json()["id"]
        r = c.http.post(
            "/v1/kb/documents",
            data={"collection_id": collection},
            files={"file": ("policy.md", POLICY, "text/markdown")},
            headers=host,
        )
        assert r.status_code == 202, r.text
        doc, card = r.json()["document"], r.json()["candidate"]
        c.drain()
        assert real_engine.collection(collection) is None  # nothing reached the engine at all

        r = c.http.post(f"/v1/candidates/{card['id']}/accept", headers=reviewer)
        assert r.status_code == 200, r.text
        deadline = time.monotonic() + 120
        while (state := _document(c, host, doc["id"]))["status"] in ("pending", "processing"):
            assert time.monotonic() < deadline, state
            c.make_jobs_due()  # the next poll is scheduled seconds ahead
            c.drain()
            time.sleep(0.5)
        assert (state["state"], state["status"]) == ("verified", "ready")

        def retrieve() -> list[dict[str, Any]]:
            r = c.http.post(
                "/v1/retrieve",
                json={"query": "how long do refunds take", "sources": ["kb"]},
                headers=host,
            )
            assert r.status_code == 200 and r.json()["degraded"] == [], r.text
            return list(r.json()["items"])

        [item] = retrieve()
        assert (item["id"], item["kind"]) == (doc["id"], "document")
        assert "Refunds take 3 to 5 business days" in item["content"]

        assert c.http.delete(f"/v1/kb/documents/{doc['id']}", headers=reviewer).status_code == 200
        assert retrieve() == []  # worker not run: the safety net
        kb, ext = c.services.providers.kb, real_engine.collection(collection)
        assert ext is not None
        assert c.call(kb.search, "refunds", [ext], top_k=5) != []  # the engine still has it
        c.drain()
        assert c.call(kb.search, "refunds", [ext], top_k=5) == []


@pytest.mark.contract
def test_switching_a_namespace_from_weknora_to_ragflow(
    migrated_url: str, weknora_engine: _Engine, ragflow_engine: _Engine
) -> None:
    """P7 acceptance (design §11): after the KB engine is switched and `resync` runs, every
    effective entry — and a parsed document, whose bytes are kept while it is live — is
    retrievable from the new engine; states, versions and history are the same; a retired entry
    stays out."""
    from llm_steward.models import ChangeLog, KbEntry
    from llm_steward.services.resync import resync

    texts = {
        "Refund time": "Refunds take 3 to 5 business days.",
        "Courier": "Parcels ship by courier within two days.",
        "Invoices": "Invoices are emailed on the first business day of each month.",
    }
    with _engine_app(migrated_url, weknora_engine) as (c, host, reviewer):
        collection = c.http.post(
            "/v1/kb/collections", json={"name": "faq", "default_risk": "low"}, headers=reviewer
        ).json()["id"]
        ids = {}
        for title, content in texts.items():
            body = {"collection_id": collection, "title": title, "content": content}
            ids[title] = c.http.post("/v1/kb/entries", json=body, headers=reviewer).json()["entry"][
                "id"
            ]
        retired = c.http.post(
            "/v1/kb/entries",
            json={"collection_id": collection, "title": "Old rule", "content": "Refunds: 30 days."},
            headers=reviewer,
        ).json()["entry"]["id"]
        c.http.post(f"/v1/kb/entries/{retired}/deprecate", headers=reviewer)
        upload = c.http.post(
            "/v1/kb/documents",
            data={"collection_id": collection},
            files={"file": ("policy.md", POLICY, "text/markdown")},
            headers=reviewer,
        )
        document = upload.json()["document"]["id"]
        _parsed(c, host, document)  # by WeKnora
        assert _retrieved(c, host, "how long do refunds take")[0] == ids["Refund time"]
        before = {i: (c.get(KbEntry, i).status, c.get(KbEntry, i).version) for i in ids.values()}
        history = c.count(ChangeLog)
        namespace = c.get(KbEntry, ids["Refund time"]).namespace_id

    with _engine_client(migrated_url, ragflow_engine) as c:
        assert _retrieved(c, host, "how long do refunds take") == []  # RAGFlow holds nothing yet
        report = c.call(
            resync, c.sessions, namespace, kb_provider="ragflow", memory_provider="native"
        )
        assert (report.entries, report.documents) == (len(ids), 1)  # the retired one stays
        assert report.documents_without_file == []
        _parsed(c, host, document)  # by RAGFlow, from the bytes kept here
        for title, content in texts.items():
            assert _retrieved(c, host, content)[0] == ids[title], title  # the best match
        assert retired not in _retrieved(c, host, "refunds 30 days")
        assert document in _retrieved(c, host, "is shipping free above 50 euros")
        after = {i: (c.get(KbEntry, i).status, c.get(KbEntry, i).version) for i in ids.values()}
        assert after == before
        assert c.count(ChangeLog) == history


def _parsed(c: Any, headers: dict[str, str], document_id: str) -> None:
    """Run the worker (polls included) until the engine has parsed the document."""
    deadline = time.monotonic() + 120
    while (state := _document(c, headers, document_id))["status"] != "ready":
        assert state["status"] != "failed" and time.monotonic() < deadline, state
        c.make_jobs_due()
        c.drain()
        time.sleep(0.5)


def _retrieved(c: Any, headers: dict[str, str], query: str) -> list[str]:
    r = c.http.post("/v1/retrieve", json={"query": query, "sources": ["kb"]}, headers=headers)
    assert r.status_code == 200 and r.json()["degraded"] == [], r.text
    return [i["id"] for i in r.json()["items"]]


@contextmanager
def _engine_app(migrated_url: str, engine: _Engine) -> Iterator[Any]:
    """The app on a real engine, with a host-app key (read/write) and a reviewer key (review
    scope)."""
    from tests.conftest import Tenant

    with _engine_client(migrated_url, engine) as c:
        t = Tenant(c, "acme")
        yield t.c, t.host, t.reviewer


@contextmanager
def _engine_client(migrated_url: str, engine: _Engine) -> Iterator[Any]:
    """The app on a real engine, on whatever the database already holds."""
    from fastapi.testclient import TestClient

    from llm_steward.main import create_app
    from tests.conftest import AppClient, test_settings

    settings = test_settings(
        migrated_url,
        **engine.settings,
        provider_timeout_ms=10_000,
        retrieve_timeout_ms=10_000,
    )
    app = create_app(settings, embedder=FakeEmbedder())
    with TestClient(app) as client:
        yield AppClient(client, app.state.services)


def _document(c: Any, headers: dict[str, str], document_id: str) -> dict[str, Any]:
    r = c.http.get(f"/v1/kb/documents/{document_id}", headers=headers)
    assert r.status_code == 200, r.text
    return dict(r.json())


def _auth(target: _WeKnoraTarget) -> dict[str, str]:
    return {"X-API-Key": target.api_key or ""}


def _weknora_kbs(target: _WeKnoraTarget) -> dict[str, str]:
    """id → name of every knowledge base in the WeKnora workspace."""
    r = httpx2.get(f"{target.url}/api/v1/knowledge-bases", headers=_auth(target))
    return {kb["id"]: kb["name"] for kb in r.json()["data"]}


def _named(listing: dict[str, str], collection_id: str) -> str | None:
    """The engine id (in an id → name listing) of a llm-steward collection, if it was ever
    created: engines name it `collection_name`."""
    name = collection_name(collection_id)
    return next((i for i, n in listing.items() if n == name), None)


# --- RAGFlow v1.0.0 (real engine, -m contract) ----------------------------------------------


class _RAGFlowTarget(BaseSettings):
    """The local RAGFlow (deploy/ragflow; its key from deploy/ragflow/bootstrap.py), read from the
    environment or backend/.env. Missing: the test fails, it never skips."""

    model_config = SettingsConfigDict(
        env_prefix="STEWARD_RAGFLOW_", env_file=".env", extra="ignore"
    )
    url: str = "http://127.0.0.1:8690"
    api_key: str | None = None


@pytest.fixture
def ragflow_target() -> Iterator[_RAGFlowTarget]:
    """The configured local RAGFlow; afterwards, every dataset the test created is deleted (listed
    before and after: governance never drops a collection, so no provider call does)."""
    target = _RAGFlowTarget()
    if not target.api_key:
        pytest.fail(
            "RAGFlow contract needs STEWARD_RAGFLOW_API_KEY (deploy/ragflow/README.md: start it, "
            "then run deploy/ragflow/bootstrap.py)"
        )
    before = set(_ragflow_datasets(target))
    yield target
    created = sorted(set(_ragflow_datasets(target)) - before)
    if created:
        httpx2.request(
            "DELETE",
            f"{target.url}/api/v1/datasets",
            json={"ids": created},
            headers=_ragflow_auth(target),
        )


@pytest.fixture
def ragflow_engine(ragflow_target: _RAGFlowTarget) -> _Engine:
    t = ragflow_target
    settings = {"kb_provider": "ragflow", "ragflow_url": t.url, "ragflow_api_key": t.api_key}
    return _Engine(settings, lambda collection: _named(_ragflow_datasets(t), collection))


@pytest.mark.contract
async def test_ragflow(ragflow_target: _RAGFlowTarget) -> None:
    t = ragflow_target
    async with httpx2.AsyncClient() as http:
        kb = RAGFlowProvider(http, t.url, t.api_key, timeout_s=10)
        assert (await kb.health()).status == "ok"
        dataset, document = (await check_lifecycle(kb)).split("/")
        assert dataset and document  # <dataset>/<document>: every document call needs both
        await check_documents(kb, settle_s=120)


@pytest.mark.contract
async def test_ragflow_refuses_a_wrong_key(ragflow_target: _RAGFlowTarget) -> None:
    async with httpx2.AsyncClient() as http:
        kb = RAGFlowProvider(http, ragflow_target.url, "ragflow-wrong", timeout_s=10)
        with pytest.raises(ProviderUnavailable):
            await kb.search("refunds", ["0" * 32], top_k=1)


def _ragflow_auth(target: _RAGFlowTarget) -> dict[str, str]:
    return {"Authorization": f"Bearer {target.api_key or ''}"}


def _ragflow_datasets(target: _RAGFlowTarget) -> dict[str, str]:
    """id → name of every dataset the key can see."""
    r = httpx2.get(
        f"{target.url}/api/v1/datasets", params={"page_size": 100}, headers=_ragflow_auth(target)
    )
    return {d["id"]: d["name"] for d in r.json()["data"]}
