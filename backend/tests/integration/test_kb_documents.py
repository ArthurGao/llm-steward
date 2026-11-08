"""KB documents (design §4, §7, §8; P3d), end to end through the app, the worker and the native KB
provider, on both dialects: a document is governed as a whole by its collection, its bytes wait in
the governance DB until the engine has them, and retrieval serves only live, parsed documents."""

import uuid
from datetime import datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from llm_steward.errors import Forbidden
from llm_steward.models import Job, KbDocumentFile
from llm_steward.models.enums import DocStatus, JobStatus, JobType
from llm_steward.providers.base import ENTRY_UPSERT
from llm_steward.storage.db import session_factory
from tests.conftest import REVIEWER, Tenant

REFUNDS = b"Refunds take 3 to 5 business days.\n\nShipping is free above 50 euros.\n"


def upload(
    t: Tenant,
    collection: str,
    data: bytes = REFUNDS,
    *,
    filename: str = "policy.md",
    as_reviewer: bool = False,
    **form: str,
) -> Any:
    headers = t.reviewer if as_reviewer else t.host
    return t.c.http.post(
        "/v1/kb/documents",
        data={"collection_id": collection, **form},
        files={"file": (filename, data, "text/markdown")},
        headers=headers,
    )


def get(t: Tenant, document_id: str) -> dict[str, Any]:
    r = t.c.http.get(f"/v1/kb/documents/{document_id}", headers=t.host)
    assert r.status_code == 200, r.text
    return dict(r.json())


def items(t: Tenant, query: str = "refunds") -> list[dict[str, Any]]:
    body = {"query": query, "sources": ["kb"]}
    r = t.c.http.post("/v1/retrieve", json=body, headers=t.host)
    assert r.status_code == 200, r.text
    return list(r.json()["items"])


def stored_files(t: Tenant) -> int:
    async def count() -> int:
        async with session_factory(t.c.services.engine)() as s:
            return int(await s.scalar(select(func.count()).select_from(KbDocumentFile)) or 0)

    return t.c.call(count)


def sync_status(t: Tenant, document_id: str) -> str:
    """The document's external_refs status at the KB provider."""
    from llm_steward.models import ExternalRef

    async def read() -> str:
        async with session_factory(t.c.services.engine)() as s:
            status = await s.scalar(
                select(ExternalRef.sync_status).where(
                    ExternalRef.object_id == uuid.UUID(document_id)
                )
            )
            return str(status)

    return t.c.call(read)


def in_provider(t: Tenant, collection: str, query: str = "refunds") -> list[str]:
    """What the KB provider itself returns (no governance filter)."""
    from llm_steward.providers.base import KbCollectionRef

    kb = t.c.services.providers.kb
    ext = t.c.call(kb.ensure_collection, KbCollectionRef(uuid.UUID(collection), t.ns, ""))
    return [h.external_id for h in t.c.call(kb.search, query, [ext], top_k=5)]


def test_a_document_goes_live_and_retrieval_serves_its_matching_chunk(t: Tenant) -> None:
    collection = t.collection(default_risk="low")
    r = upload(t, collection, title="Customer policy")
    assert r.status_code == 202, r.text
    doc = r.json()["document"]
    assert (doc["state"], doc["status"], doc["title"], doc["filename"]) == (
        "active",
        "pending",
        "Customer policy",
        "policy.md",
    )
    assert (doc["size_bytes"], r.json()["candidate"]) == (len(REFUNDS), None)
    assert stored_files(t) == 1  # waiting for the worker

    t.c.drain()
    assert get(t, doc["id"])["status"] == "ready"
    assert stored_files(t) == 0  # the engine has it: the bytes are gone
    [hit] = items(t)
    assert (hit["id"], hit["kind"], hit["title"]) == (doc["id"], "document", "Customer policy")
    assert hit["content"] == "Refunds take 3 to 5 business days."  # the chunk, not the file
    assert [i["content"] for i in items(t, "shipping")] == ["Shipping is free above 50 euros."]


def test_the_title_defaults_to_the_file_name_and_is_screened(t: Tenant) -> None:
    collection = t.collection(default_risk="low")
    doc = upload(t, collection).json()["document"]
    assert doc["title"] == "policy.md"
    doc = upload(t, collection, b"other", title="Mail bob@example.com").json()["document"]
    assert "bob@example.com" not in doc["title"]  # personal data redacted, like an entry title


def test_a_high_risk_document_waits_for_a_reviewer_and_never_reaches_the_provider(
    t: Tenant,
) -> None:
    collection = t.collection(default_risk="high")
    r = upload(t, collection)
    assert r.status_code == 202, r.text
    doc, candidate = r.json()["document"], r.json()["candidate"]
    assert doc["state"] == "draft"
    assert (candidate["target_type"], candidate["target_id"], candidate["stage"]) == (
        "kb_document",
        doc["id"],
        "awaiting_review",
    )
    t.c.drain()
    assert in_provider(t, collection) == [] and items(t) == []
    queue = t.c.http.get("/v1/candidates?stage=awaiting_review", headers=t.host).json()["items"]
    assert [c["id"] for c in queue] == [candidate["id"]]

    assert t.post(f"/v1/candidates/{candidate['id']}/accept").status_code == 403  # host app
    r = t.post(f"/v1/candidates/{candidate['id']}/accept", as_reviewer=True)
    assert r.status_code == 200, r.text
    assert get(t, doc["id"])["state"] == "verified"
    t.c.drain()
    assert [i["id"] for i in items(t)] == [doc["id"]]
    assert items(t)[0]["verified"] is True


def test_a_reviewers_upload_is_verified_at_once(t: Tenant) -> None:
    r = upload(t, t.collection(default_risk="high"), as_reviewer=True)
    assert (r.json()["document"]["state"], r.json()["candidate"]) == ("verified", None)


def test_a_rejected_document_drops_its_bytes_and_stays_out(t: Tenant) -> None:
    collection = t.collection(default_risk="high")
    r = upload(t, collection)
    candidate, doc = r.json()["candidate"], r.json()["document"]
    r = t.post(f"/v1/candidates/{candidate['id']}/reject", as_reviewer=True)
    assert r.status_code == 200, r.text
    assert get(t, doc["id"])["state"] == "rejected"
    assert stored_files(t) == 0
    t.c.drain()
    assert in_provider(t, collection) == []


def test_a_deleted_document_leaves_retrieval_at_once_and_the_provider_when_the_worker_runs(
    t: Tenant,
) -> None:
    collection = t.collection(default_risk="low")
    doc = upload(t, collection).json()["document"]
    t.c.drain()
    assert t.c.http.delete(f"/v1/kb/documents/{doc['id']}", headers=t.host).status_code == 403
    r = t.c.http.delete(f"/v1/kb/documents/{doc['id']}", headers=t.reviewer)
    assert r.status_code == 200, r.text
    deleted = r.json()
    assert (deleted["state"], deleted["valid_to"] is not None) == ("deprecated", True)
    # the worker has not run: the provider still has it, the safety net keeps it out
    assert in_provider(t, collection) != [] and items(t) == []
    assert sync_status(t, doc["id"]) == "deleting"
    # ... but a question about the past still sees what was live then
    then = {"query": "refunds", "sources": ["kb"], "as_of": doc["created_at"]}
    r = t.c.http.post("/v1/retrieve", json=then, headers=t.host)
    assert [i["id"] for i in r.json()["items"]] == [doc["id"]]
    after = datetime.fromisoformat(deleted["valid_to"]) + timedelta(seconds=1)
    r = t.c.http.post("/v1/retrieve", json={**then, "as_of": after.isoformat()}, headers=t.host)
    assert r.json()["items"] == []  # no longer valid by then
    before = datetime.fromisoformat(doc["created_at"]) - timedelta(seconds=1)
    r = t.c.http.post("/v1/retrieve", json={**then, "as_of": before.isoformat()}, headers=t.host)
    assert r.json()["items"] == []  # not uploaded yet then
    t.c.drain()
    assert in_provider(t, collection) == []
    assert sync_status(t, doc["id"]) == "deleted"


def test_deleting_a_waiting_document_rejects_it(t: Tenant) -> None:
    r = upload(t, t.collection(default_risk="high"))
    doc, candidate = r.json()["document"], r.json()["candidate"]
    r = t.c.http.delete(f"/v1/kb/documents/{doc['id']}", headers=t.reviewer)
    assert r.json()["state"] == "rejected"
    queue = t.c.http.get("/v1/candidates", headers=t.host).json()["items"]
    assert candidate["id"] not in [c["id"] for c in queue]  # no longer waiting for anyone
    assert stored_files(t) == 0


def test_the_same_file_twice_in_a_collection_is_a_conflict_until_the_first_is_gone(
    t: Tenant,
) -> None:
    collection = t.collection(default_risk="low")
    first = upload(t, collection).json()["document"]
    r = upload(t, collection, filename="copy.md")
    assert r.status_code == 409 and first["id"] in r.json()["detail"]
    assert upload(t, t.collection(default_risk="low")).status_code == 202  # another collection
    t.c.http.delete(f"/v1/kb/documents/{first['id']}", headers=t.reviewer)
    assert upload(t, collection).status_code == 202


def test_uploads_are_checked_before_anything_is_stored(
    t: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = t.collection(default_risk="low")
    assert upload(t, collection, filename="tool.exe").status_code == 422
    assert upload(t, collection, b"").status_code == 422
    monkeypatch.setattr(t.c.services.documents, "max_bytes", len(REFUNDS) - 1)
    assert upload(t, collection).status_code == 413
    monkeypatch.setattr(t.c.services.documents, "max_bytes", 10**6)
    other = t.c.new_key("other")
    r = t.c.http.post(
        "/v1/kb/documents",
        data={"collection_id": collection},
        files={"file": ("a.md", REFUNDS, "text/markdown")},
        headers=other,
    )
    assert r.status_code == 404  # another namespace's collection
    monkeypatch.setattr(t.c.services.providers.kb, "capabilities", frozenset({ENTRY_UPSERT}))
    assert upload(t, collection).status_code == 422  # this KB provider cannot parse documents
    assert stored_files(t) == 0


def test_reads_are_scoped_to_the_namespace_and_listed_newest_first(t: Tenant) -> None:
    collection = t.collection(default_risk="low")
    a = upload(t, collection, b"a").json()["document"]["id"]
    b = upload(t, collection, b"b").json()["document"]["id"]
    r = t.c.http.get(f"/v1/kb/documents?collection_id={collection}&limit=1", headers=t.host)
    assert [d["id"] for d in r.json()["items"]] == [b]
    cursor = r.json()["next_cursor"]
    r = t.c.http.get(f"/v1/kb/documents?cursor={cursor}", headers=t.host)
    assert [d["id"] for d in r.json()["items"]] == [a]
    r = t.c.http.get("/v1/kb/documents?state=draft", headers=t.host)
    assert r.json()["items"] == []
    other = t.c.new_key("other")
    assert t.c.http.get(f"/v1/kb/documents/{a}", headers=other).status_code == 404
    assert t.c.http.delete(f"/v1/kb/documents/{a}", headers=other).status_code in (403, 404)


def test_the_parse_status_is_polled_until_the_engine_settles(
    t: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    collection = t.collection(default_risk="low")
    replies = [DocStatus.PROCESSING, DocStatus.READY]

    async def status(external_id: str) -> DocStatus:
        return replies.pop(0)

    monkeypatch.setattr(t.c.services.providers.kb, "document_status", status)
    doc = upload(t, collection).json()["document"]
    t.c.drain()
    assert get(t, doc["id"])["status"] == "processing"
    assert items(t) == []  # not parsed yet: not served
    assert stored_files(t) == 1  # kept until the engine is done with it

    async def polls() -> list[Any]:
        async with session_factory(t.c.services.engine)() as s:
            return list(
                await s.scalars(
                    select(Job.run_after).where(
                        Job.type == JobType.DOC_STATUS, Job.status == JobStatus.PENDING
                    )
                )
            )

    assert len(t.c.call(polls)) == 1  # the next poll is scheduled, not claimable yet
    t.c.make_jobs_due()
    t.c.drain()
    assert get(t, doc["id"])["status"] == "ready" and replies == []
    assert [i["id"] for i in items(t)] == [doc["id"]]


def test_a_document_the_engine_cannot_parse_is_failed_for_good(
    t: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def status(external_id: str) -> DocStatus:
        return DocStatus.FAILED

    monkeypatch.setattr(t.c.services.providers.kb, "document_status", status)
    doc = upload(t, t.collection(default_risk="low")).json()["document"]
    t.c.drain()
    got = get(t, doc["id"])
    assert (got["status"], got["state"], got["error"]) == ("failed", "active", "parsing failed")
    assert stored_files(t) == 0 and items(t) == []
    assert in_provider(t, doc["collection_id"]) == []  # whatever the engine kept is removed


def test_a_document_still_parsing_after_the_deadline_is_given_up(
    t: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def status(external_id: str) -> DocStatus:
        return DocStatus.PROCESSING

    monkeypatch.setattr(t.c.services.providers.kb, "document_status", status)
    doc = upload(t, t.collection(default_risk="low")).json()["document"]
    t.c.drain()
    assert get(t, doc["id"])["status"] == "processing"

    async def started_long_ago(engine: Any) -> None:
        from datetime import timedelta

        from llm_steward.models.base import utcnow

        async with session_factory(engine)() as s, s.begin():
            [job] = list(
                await s.scalars(
                    select(Job).where(
                        Job.type == JobType.DOC_STATUS, Job.status == JobStatus.PENDING
                    )
                )
            )
            since = (utcnow() - timedelta(minutes=31)).isoformat()
            job.payload = {**job.payload, "since": since}
            job.run_after = utcnow()

    t.c.call(started_long_ago, t.c.services.engine)
    t.c.drain()
    got = get(t, doc["id"])
    assert (got["status"], got["error"]) == ("failed", "parsing did not finish in time")
    assert stored_files(t) == 0 and in_provider(t, doc["collection_id"]) == []


def test_a_document_deleted_while_it_uploads_is_taken_back_out(
    t: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The delete lands while the engine receives the file: the remove job finds no engine id
    yet, so the upload itself must undo what it just stored."""
    from llm_steward.services.governance_service import RetireKbDocument

    collection = t.collection(default_risk="low")
    kb = t.c.services.providers.kb
    original = kb.ingest_document

    async def ingest(doc: Any, collection_ext_id: str) -> str:
        await t.c.services.governance.apply(RetireKbDocument(t.ns, doc.document_id), REVIEWER)
        await t.c.services.worker.drain()  # another worker runs the remove job first
        return await original(doc, collection_ext_id)

    monkeypatch.setattr(kb, "ingest_document", ingest)
    doc = upload(t, collection).json()["document"]
    t.c.drain()
    assert get(t, doc["id"])["state"] == "deprecated"
    assert in_provider(t, collection) == []


def test_only_a_reviewer_can_take_a_document_out(t: Tenant) -> None:
    """Enforced in governance, not only by the API's review scope."""
    from llm_steward.models.enums import ActorType
    from llm_steward.services.governance_service import Actor, RetireKbDocument

    doc = upload(t, t.collection(default_risk="low")).json()["document"]
    host = Actor(ActorType.HOST_APP, "api_key:host")
    op = RetireKbDocument(t.ns, uuid.UUID(doc["id"]))
    with pytest.raises(Forbidden):
        t.c.call(t.c.services.governance.apply, op, host)
    assert get(t, doc["id"])["state"] == "active"


def test_an_undo_that_fails_is_retried_until_the_engine_copy_is_gone(
    t: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Deleted while uploading, the remove job already ran (no engine id yet), and taking the new
    copy back out fails once: the removal must be retried, not dropped with the upload job."""
    from llm_steward.errors import ProviderUnavailable
    from llm_steward.services.governance_service import RetireKbDocument

    collection = t.collection(default_risk="low")
    kb = t.c.services.providers.kb
    ingest, remove = kb.ingest_document, kb.remove
    failures: list[Exception] = []

    async def ingest_racing(doc: Any, collection_ext_id: str) -> str:
        await t.c.services.governance.apply(RetireKbDocument(t.ns, doc.document_id), REVIEWER)
        await t.c.services.worker.drain()  # the remove job runs first and finds nothing
        external_id = await ingest(doc, collection_ext_id)
        failures.append(ProviderUnavailable("engine down"))  # the next removal fails once
        return external_id

    async def flaky_remove(external_id: str) -> None:
        if failures:
            raise failures.pop()
        await remove(external_id)

    monkeypatch.setattr(kb, "ingest_document", ingest_racing)
    monkeypatch.setattr(kb, "remove", flaky_remove)
    doc = upload(t, collection).json()["document"]
    t.c.drain()
    t.c.make_jobs_due()  # the retry's backoff
    t.c.drain()
    assert failures == []  # the failing removal did happen
    assert in_provider(t, collection) == []
    assert sync_status(t, doc["id"]) == "deleted"


def test_a_late_status_poll_does_not_mark_a_deleted_document_synced(
    t: Tenant, monkeypatch: pytest.MonkeyPatch
) -> None:
    from llm_steward.services.governance_service import RetireKbDocument

    collection = t.collection(default_risk="low")
    kb = t.c.services.providers.kb
    status = kb.document_status

    async def status_racing(external_id: str) -> DocStatus:
        reply = await status(external_id)  # READY
        [doc_id] = [d["id"] for d in listed]
        await t.c.services.governance.apply(RetireKbDocument(t.ns, uuid.UUID(doc_id)), REVIEWER)
        await t.c.services.worker.drain()  # the removal completes before the poll records
        return reply

    listed: list[dict[str, Any]] = []
    monkeypatch.setattr(kb, "document_status", status_racing)
    listed.append(upload(t, collection).json()["document"])
    t.c.drain()
    assert in_provider(t, collection) == []
    assert sync_status(t, listed[0]["id"]) == "deleted"
