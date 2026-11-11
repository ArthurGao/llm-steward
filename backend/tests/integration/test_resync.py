"""`steward resync` (design §11 P7): after the KB or memory engine is switched, every effective
object that the current engine does not hold yet is queued for the Syncer; governance state and
history are untouched. Both dialects; the switch itself (WeKnora → RAGFlow) runs for real in
tests/contract/test_kb_contract.py under -m contract."""

import uuid
from typing import Any

import pytest
from sqlalchemy import func, select

from llm_steward.models import ChangeLog, ExternalRef, KbDocumentFile, KbEntry, Memory
from llm_steward.models.enums import JobType, KbStatus, ObjectType, SyncStatus
from llm_steward.services.governance_service import CreateCollection, TransitionKbEntry
from llm_steward.services.resync import ResyncReport, resync
from tests.conftest import (
    AppClient,
    Tenant,
    fake_network,
    kb_entry_op,
    post_memory,
    running_app,
)


def _resync(c: AppClient, ns: uuid.UUID, **kw: Any) -> ResyncReport:
    options = {"kb_provider": "ragflow", "memory_provider": "mem0", **kw}
    return c.call(resync, c.sessions, ns, **options)


def _refs(c: AppClient, provider: str) -> dict[uuid.UUID, SyncStatus]:
    async def read() -> dict[uuid.UUID, SyncStatus]:
        async with c.sessions() as s:
            rows = await s.execute(
                select(ExternalRef.object_id, ExternalRef.sync_status).where(
                    ExternalRef.provider == provider
                )
            )
            return {object_id: status for object_id, status in rows.all()}

    return c.call(read)


def _payloads(c: AppClient, job_type: JobType, key: str) -> list[str]:
    return [j.payload[key] for j in c.jobs(job_type)]


def test_only_effective_entries_the_new_engine_lacks_are_queued(app_client: AppClient) -> None:
    c = app_client
    _, ns = c.new_tenant("acme")
    collection = c.apply(CreateCollection(ns, "faq")).id
    created = (KbStatus.DRAFT, KbStatus.ACTIVE, KbStatus.VERIFIED)
    live = {status: c.apply(kb_entry_op(ns, collection, status)) for status in created}
    retired = c.apply(kb_entry_op(ns, collection, KbStatus.ACTIVE, title="Old refund rule"))
    c.apply(TransitionKbEntry(ns, retired.id, KbStatus.DEPRECATED))
    c.drain()  # the effective ones reach the current engine (native)
    queued_before = len(c.jobs(JobType.KB_UPSERT))
    history = c.count(ChangeLog)

    report = _resync(c, ns, memory=False)

    effective = {live[KbStatus.ACTIVE].id, live[KbStatus.VERIFIED].id}
    assert report.entries == 2
    new_jobs = c.jobs(JobType.KB_UPSERT)[queued_before:]
    assert {uuid.UUID(j.payload["entry_id"]) for j in new_jobs} == effective
    # (the collection's ref there is opened by the Syncer when it first pushes into it)
    assert _refs(c, "ragflow") == dict.fromkeys(effective, SyncStatus.PENDING)
    # governance is untouched: same states, same history, the old engine's refs kept
    assert {e.status for e in (c.get(KbEntry, i) for i in effective)} == {
        KbStatus.ACTIVE,
        KbStatus.VERIFIED,
    }
    assert c.count(ChangeLog) == history
    assert set(_refs(c, "native")) >= effective


def test_resync_again_queues_nothing_new(app_client: AppClient) -> None:
    c = app_client
    _, ns = c.new_tenant("acme")
    collection = c.apply(CreateCollection(ns, "faq")).id
    c.apply(kb_entry_op(ns, collection, KbStatus.ACTIVE))
    assert _resync(c, ns, memory=False).entries == 1
    assert _resync(c, ns, memory=False).entries == 0  # already queued (pending)


def test_an_entry_synced_at_an_older_version_is_queued_again(app_client: AppClient) -> None:
    """Synced, then edited while the switch was under way: the new engine has a stale copy."""
    c = app_client
    _, ns = c.new_tenant("acme")
    collection = c.apply(CreateCollection(ns, "faq")).id
    entry = c.apply(kb_entry_op(ns, collection, KbStatus.ACTIVE))

    async def synced_at(version: int) -> None:
        async with c.sessions() as s, s.begin():
            s.add(
                ExternalRef(
                    namespace_id=ns,
                    object_type=ObjectType.KB_ENTRY,
                    object_id=entry.id,
                    provider="ragflow",
                    external_id="ds/doc",
                    sync_status=SyncStatus.SYNCED,
                    synced_version=version,
                )
            )

    c.call(synced_at, entry.version - 1)
    assert _resync(c, ns, memory=False).entries == 1


def test_resync_stays_in_its_namespace(app_client: AppClient) -> None:
    c = app_client
    _, ns = c.new_tenant("acme")
    _, other = c.new_tenant("other")
    for namespace in (ns, other):
        collection = c.apply(CreateCollection(namespace, "faq")).id
        c.apply(kb_entry_op(namespace, collection, KbStatus.ACTIVE))
    assert _resync(c, ns, memory=False).entries == 1
    assert all(j.namespace_id == ns for j in c.jobs(JobType.KB_UPSERT)[-1:])


def test_active_memories_are_queued_for_the_new_memory_engine(app_client: AppClient) -> None:
    c = app_client
    h, ns = c.new_tenant("acme")
    kept = post_memory(c, h, "likes green tea", user_id="u1")
    gone = post_memory(c, h, "old address", user_id="u1")
    c.http.delete(f"/v1/memories/{gone}", headers=h)
    c.drain()
    queued_before = len(c.jobs(JobType.MEMORY_UPSERT))

    report = _resync(c, ns, kb=False)

    assert (report.memories, report.entries) == (1, 0)
    assert _payloads(c, JobType.MEMORY_UPSERT, "memory_id")[queued_before:] == [kept]
    assert _refs(c, "mem0") == {uuid.UUID(kept): SyncStatus.PENDING}
    assert c.get(Memory, kept).content == "likes green tea"


def test_documents_move_while_their_file_is_held_and_are_reported_otherwise(
    app_client: AppClient,
) -> None:
    """The bytes are dropped once the engine has parsed a document (design §5): such a document
    cannot be handed to another engine, so resync names it instead of pretending."""
    c = app_client
    t = Tenant(c, "acme")
    collection = t.collection(default_risk="low")
    parsed = t.upload(collection, b"# Refunds\n\nRefunds take five days.", as_reviewer=True)
    c.settle()  # parsed by the native engine: its file is gone
    held = t.upload(collection, b"# Shipping\n\nWe ship to Norway.", filename="ship.md")
    parsed_id, held_id = parsed.json()["document"]["id"], held.json()["document"]["id"]
    assert c.get(KbDocumentFile, held_id) is not None and c.get(KbDocumentFile, parsed_id) is None
    queued_before = len(c.jobs(JobType.DOC_INGEST))

    report = _resync(c, t.ns, memory=False)

    assert report.documents == 1
    assert report.documents_without_file == [uuid.UUID(parsed_id)]
    assert _payloads(c, JobType.DOC_INGEST, "document_id")[queued_before:] == [held_id]


def test_a_namespace_with_nothing_effective_queues_nothing(app_client: AppClient) -> None:
    c = app_client
    _, ns = c.new_tenant("acme")

    async def jobs() -> int:
        from llm_steward.models import Job

        async with c.sessions() as s:
            return int(await s.scalar(select(func.count()).select_from(Job)) or 0)

    before = c.call(jobs)
    assert _resync(c, ns) == ResyncReport()
    assert c.call(jobs) == before


def test_the_cli_resyncs_every_namespace_or_the_ones_named(
    cli_db: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`steward resync` reads the engines from STEWARD_KB_PROVIDER / STEWARD_MEMORY_PROVIDER,
    exactly as the app does after the switch."""
    from typer.testing import CliRunner

    from llm_steward.cli import app

    runner = CliRunner()
    with running_app(cli_db) as c:
        for slug in ("acme", "beta"):
            _, ns = c.new_tenant(slug)
            collection = c.apply(CreateCollection(ns, "faq")).id
            c.apply(kb_entry_op(ns, collection, KbStatus.ACTIVE))
    monkeypatch.setenv("STEWARD_KB_PROVIDER", "ragflow")
    monkeypatch.setenv("STEWARD_MEMORY_PROVIDER", "native")

    one = runner.invoke(app, ["resync", "--ns", "acme", "--no-memory"])
    assert (one.exit_code, one.output) == (
        0,
        "acme: queued 1 entries, 0 documents for ragflow\n",
    )
    every = runner.invoke(app, ["resync"])
    assert every.exit_code == 0, every.output
    assert every.output.splitlines() == [
        "acme: queued 0 entries, 0 documents for ragflow; 0 memories for native",
        "beta: queued 1 entries, 0 documents for ragflow; 0 memories for native",
    ]
    unknown = runner.invoke(app, ["resync", "--ns", "nope"])
    assert unknown.exit_code == 1 and "nope" in unknown.output


def test_a_new_entry_reaches_an_engine_that_has_no_ref_for_its_collection_yet(
    app_client: AppClient,
) -> None:
    """After a switch, before anyone runs resync, a collection has no ref at the new engine. A new
    entry in it must still be pushed: the Syncer opens the collection's ref itself."""
    from sqlalchemy import delete

    c = app_client
    _, ns = c.new_tenant("acme")
    collection = c.apply(CreateCollection(ns, "faq")).id

    async def forget_collection_ref() -> None:
        async with c.sessions() as s, s.begin():
            await s.execute(
                delete(ExternalRef).where(
                    ExternalRef.object_type == ObjectType.KB_COLLECTION,
                    ExternalRef.object_id == collection,
                )
            )

    c.call(forget_collection_ref)
    entry = c.apply(kb_entry_op(ns, collection, KbStatus.ACTIVE))
    c.drain()
    assert _refs(c, "native")[entry.id] == SyncStatus.SYNCED
    assert _refs(c, "native")[collection] == SyncStatus.SYNCED


def test_a_url_import_still_being_fetched_is_neither_queued_nor_reported(
    app_client: AppClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No bytes yet, but not lost either: its fetch hands it to the configured engine."""
    import httpx2

    c = app_client
    fake_network(c, monkeypatch, lambda request: httpx2.Response(404))
    t = Tenant(c, "acme")
    data = {"collection_id": t.collection(default_risk="low"), "url": "https://example.com/a.md"}
    assert c.http.post("/v1/kb/documents", data=data, headers=t.host).status_code == 202
    assert _resync(c, t.ns, memory=False) == ResyncReport()  # the fetch job is still queued
