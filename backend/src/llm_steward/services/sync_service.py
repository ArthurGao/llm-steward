"""Syncer: consumes outbox jobs and pushes effective objects to the providers.

GovernanceService opened each object's `external_refs` row in the write transaction; the Syncer only
records outcomes on it. Every handler is idempotent (a job may run twice after a lost lease): it
re-reads the object, skips work that no longer applies, and keys provider writes by the steward id.

Provider calls happen outside any database transaction, so the object can change while one is in
flight. An upsert therefore confirms its result under a row lock on the object (the same lock
GovernanceService takes to change it): still effective at the same version → synced; retired in
the meantime → the push is undone; edited in the meantime → another upsert is queued. A late
upsert can never leave retired or outdated content in a provider.

Documents (P3d) are parsed in the engine's own time: `kb.doc_ingest` hands the bytes over and
records the engine's id at once, `kb.doc_status` polls with a growing delay until the engine reports
ready or failed. The parse status and the waiting bytes are sync state, recorded here like
`external_refs`; the document's governed state changes only through GovernanceService.
"""

import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any, Literal

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import ProviderUnavailable
from llm_steward.models import (
    ExternalRef,
    KbCollection,
    KbDocument,
    KbDocumentFile,
    KbEntry,
    Memory,
)
from llm_steward.models.base import Base, utcnow
from llm_steward.models.enums import (
    EFFECTIVE_KB_STATUSES,
    DocStatus,
    JobType,
    MemoryStatus,
    ObjectType,
    SyncStatus,
)
from llm_steward.providers.base import (
    DocumentParser,
    KbCollectionRef,
    KbDocumentUpload,
    KbEntryDoc,
    Message,
    Providers,
    Scope,
    document_parser,
)
from llm_steward.services.job_queue import ClaimedJob, describe_error, enqueue

Outcome = Literal["synced", "retired", "outdated"]
# Parse-status polls: at once, then 2 s doubling up to a minute; a document still parsing after
# half an hour is given up as failed (and removed from the engine).
FIRST_POLL = timedelta(seconds=2)
MAX_POLL = timedelta(minutes=1)
PARSE_DEADLINE = timedelta(minutes=30)
PARSE_FAILED = "parsing failed"
PARSE_TIMED_OUT = "parsing did not finish in time"


def _ref_key(object_type: ObjectType, object_id: uuid.UUID, provider: str) -> Any:
    return (
        ExternalRef.object_type == object_type,
        ExternalRef.object_id == object_id,
        ExternalRef.provider == provider,
    )


class SyncService:
    JOB_TYPES = (
        JobType.MEMORY_UPSERT,
        JobType.MEMORY_REMOVE,
        JobType.KB_UPSERT,
        JobType.KB_REMOVE,
        JobType.DOC_INGEST,
        JobType.DOC_STATUS,
        JobType.DOC_REMOVE,
    )

    def __init__(self, sessions: async_sessionmaker[AsyncSession], providers: Providers) -> None:
        self._sessions = sessions
        self._providers = providers

    async def handle(self, job: ClaimedJob) -> None:
        match job.type:
            case JobType.MEMORY_UPSERT:
                await self._memory_upsert(uuid.UUID(job.payload["memory_id"]))
            case JobType.MEMORY_REMOVE:
                await self._memory_remove(uuid.UUID(job.payload["memory_id"]))
            case JobType.KB_UPSERT:
                await self._kb_upsert(uuid.UUID(job.payload["entry_id"]))
            case JobType.KB_REMOVE:
                await self._kb_remove(uuid.UUID(job.payload["entry_id"]))
            case JobType.DOC_INGEST:
                await self._doc_ingest(uuid.UUID(job.payload["document_id"]))
            case JobType.DOC_STATUS:
                await self._doc_status(uuid.UUID(job.payload["document_id"]), job.payload)
            case JobType.DOC_REMOVE:
                await self._doc_remove(uuid.UUID(job.payload["document_id"]))
            case _:
                raise ValueError(f"not a sync job: {job.type!r}")

    # memories

    async def _memory_upsert(self, memory_id: uuid.UUID) -> None:
        provider = self._providers.memory
        async with self._sessions() as s:
            memory = await s.get(Memory, memory_id)
            if memory is None or memory.status != MemoryStatus.ACTIVE:
                return  # deleted/superseded before it was synced: the remove job owns it now
            existing = await self._external_id(s, ObjectType.MEMORY, memory_id, provider.name)
        scope = Scope(memory.namespace_id, memory.user_id, memory.agent_id, memory.session_id)
        async with self._recording(ObjectType.MEMORY, memory_id, provider.name):
            if existing:
                await provider.update(existing, memory.content)
                external_id = existing
            else:
                [event, *_] = await provider.add(
                    [Message("user", memory.content)],
                    scope,
                    infer=False,
                    metadata={"steward_id": str(memory_id)},
                )
                external_id = event.external_id
        outcome = await self._confirm(
            Memory, memory_id, provider.name, external_id, version=None, job=JobType.MEMORY_UPSERT
        )
        if outcome == "retired":  # deleted while we were pushing it: take it back out
            async with self._recording(ObjectType.MEMORY, memory_id, provider.name):
                await provider.delete(external_id)
            await self._retired(ObjectType.MEMORY, memory_id, provider.name, external_id)

    async def _memory_remove(self, memory_id: uuid.UUID) -> None:
        provider = self._providers.memory
        await self._take_out(ObjectType.MEMORY, memory_id, provider.name, provider.delete)

    async def _take_out(
        self,
        object_type: ObjectType,
        object_id: uuid.UUID,
        provider: str,
        remove: Callable[[str], Awaitable[None]],
    ) -> None:
        """Remove the object's copy from the provider (if it ever got one); the ref is DELETED."""
        async with self._sessions() as s:
            external_id = await self._external_id(s, object_type, object_id, provider)
        if external_id is not None:  # None: it never reached the provider
            async with self._recording(object_type, object_id, provider):
                await remove(external_id)
        await self._set(object_type, object_id, provider, status=SyncStatus.DELETED)

    # knowledge base

    async def _kb_upsert(self, entry_id: uuid.UUID) -> None:
        provider = self._providers.kb
        async with self._sessions() as s:
            entry = await s.get(KbEntry, entry_id)
            if entry is None or entry.status not in EFFECTIVE_KB_STATUSES:
                return  # no longer effective: never push it (rule 3)
            collection = await s.get(KbCollection, entry.collection_id)
            assert collection is not None
            collection_ext = await self._external_id(
                s, ObjectType.KB_COLLECTION, collection.id, provider.name
            )
            existing = await self._external_id(s, ObjectType.KB_ENTRY, entry.id, provider.name)
        doc = KbEntryDoc(
            entry.id, entry.namespace_id, entry.title, entry.content, entry.version, entry.tags
        )
        async with self._recording(ObjectType.KB_ENTRY, entry_id, provider.name):
            collection_ext = await self._ensure_collection(collection, collection_ext)
            external_id = await provider.upsert_entry(doc, collection_ext, existing)
        outcome = await self._confirm(
            KbEntry,
            entry_id,
            provider.name,
            external_id,
            version=doc.version,
            job=JobType.KB_UPSERT,
        )
        if outcome == "retired":  # deprecated while we were pushing it: take it back out
            async with self._recording(ObjectType.KB_ENTRY, entry_id, provider.name):
                await provider.remove(external_id)
            await self._retired(ObjectType.KB_ENTRY, entry_id, provider.name, external_id)

    async def _kb_remove(self, entry_id: uuid.UUID) -> None:
        provider = self._providers.kb
        async with self._sessions() as s:
            entry = await s.get(KbEntry, entry_id)
        if entry is not None and entry.status in EFFECTIVE_KB_STATUSES:
            return  # effective again since the job was queued: keep it
        await self._take_out(ObjectType.KB_ENTRY, entry_id, provider.name, provider.remove)

    async def _ensure_collection(self, collection: KbCollection, known: str | None) -> str:
        """The collection's id in the KB provider; created there (once) on its first object."""
        if known is not None:
            return known
        provider = self._providers.kb
        ext = await provider.ensure_collection(
            KbCollectionRef(collection.id, collection.namespace_id, collection.name)
        )
        await self._set(ObjectType.KB_COLLECTION, collection.id, provider.name, synced=ext)
        return ext

    # documents

    def _parser(self) -> DocumentParser:
        parser = document_parser(self._providers.kb)
        if parser is None:  # the provider changed since the upload was accepted
            raise ProviderUnavailable(f"{self._providers.kb.name} cannot parse documents")
        return parser

    async def _doc_ingest(self, document_id: uuid.UUID) -> None:
        provider, parser = self._providers.kb, self._parser()
        async with self._sessions() as s:
            doc = await s.get(KbDocument, document_id)
            if doc is None or doc.state not in EFFECTIVE_KB_STATUSES:
                return  # taken out: the remove job owns it
            file = await s.get(KbDocumentFile, document_id)  # the bytes: read only when needed
            if file is None:
                return  # already handed over
            collection = await s.get(KbCollection, doc.collection_id)
            assert collection is not None
            collection_ext = await self._external_id(
                s, ObjectType.KB_COLLECTION, collection.id, provider.name
            )
            upload = KbDocumentUpload(
                doc.id, doc.namespace_id, doc.title, doc.filename, doc.mime, file.data
            )
        async with self._recording(ObjectType.KB_DOCUMENT, document_id, provider.name):
            collection_ext = await self._ensure_collection(collection, collection_ext)
            external_id = await parser.ingest_document(upload, collection_ext)
        async with self._sessions() as s, s.begin():
            # the engine holds a copy now: record its id before anything else (still pending)
            doc = await s.scalar(
                select(KbDocument).where(KbDocument.id == document_id).with_for_update()
            )
            await s.execute(
                update(ExternalRef)
                .where(*_ref_key(ObjectType.KB_DOCUMENT, document_id, provider.name))
                .values(external_id=external_id, last_error=None)
            )
            live = doc is not None and doc.state in EFFECTIVE_KB_STATUSES
            if live:
                assert doc is not None
                payload = {"document_id": str(document_id), "since": utcnow().isoformat()}
                enqueue(s, doc.namespace_id, JobType.DOC_STATUS, payload)  # first poll at once
        if not live:  # taken out while it was uploading: undo the upload
            async with self._recording(ObjectType.KB_DOCUMENT, document_id, provider.name):
                await provider.remove(external_id)
            await self._retired(ObjectType.KB_DOCUMENT, document_id, provider.name, external_id)

    async def _doc_status(self, document_id: uuid.UUID, payload: dict[str, Any]) -> None:
        provider, parser = self._providers.kb, self._parser()
        async with self._sessions() as s:
            doc = await s.get(KbDocument, document_id)
            external_id = await self._external_id(
                s, ObjectType.KB_DOCUMENT, document_id, provider.name
            )
        if doc is None or doc.state not in EFFECTIVE_KB_STATUSES or external_id is None:
            return
        async with self._recording(ObjectType.KB_DOCUMENT, document_id, provider.name):
            status = await parser.document_status(external_id)
        since = datetime.fromisoformat(payload["since"])
        error = PARSE_FAILED if status == DocStatus.FAILED else None
        if status not in (DocStatus.READY, DocStatus.FAILED) and utcnow() - since > PARSE_DEADLINE:
            status, error = DocStatus.FAILED, PARSE_TIMED_OUT
        ref: dict[str, Any] = {"last_error": error}
        if status == DocStatus.READY:
            ref = _synced(external_id, 1)
        elif status == DocStatus.FAILED:
            ref["sync_status"] = SyncStatus.FAILED
        async with self._sessions() as s, s.begin():
            doc = await s.scalar(
                select(KbDocument).where(KbDocument.id == document_id).with_for_update()
            )
            if doc is None:
                return
            doc.status, doc.error = status, error
            await s.execute(
                update(ExternalRef)
                .where(*_ref_key(ObjectType.KB_DOCUMENT, document_id, provider.name))
                .values(ref)
            )
            if status in (DocStatus.READY, DocStatus.FAILED):  # the engine is done with the bytes
                await s.execute(
                    delete(KbDocumentFile).where(KbDocumentFile.document_id == document_id)
                )
            if status == DocStatus.FAILED:  # final: take the engine's copy out too
                enqueue(s, doc.namespace_id, JobType.DOC_REMOVE, {"document_id": str(doc.id)})
            elif status != DocStatus.READY:
                polls = int(payload.get("polls", 0)) + 1
                delay = min(FIRST_POLL * 2 ** (polls - 1), MAX_POLL)
                again = {**payload, "polls": polls}
                enqueue(s, doc.namespace_id, JobType.DOC_STATUS, again, delay=delay)

    async def _doc_remove(self, document_id: uuid.UUID) -> None:
        provider = self._providers.kb
        async with self._sessions() as s:
            doc = await s.get(KbDocument, document_id)
        if (
            doc is not None
            and doc.state in EFFECTIVE_KB_STATUSES
            and doc.status != DocStatus.FAILED
        ):
            return  # live again since the job was queued: keep it
        await self._take_out(ObjectType.KB_DOCUMENT, document_id, provider.name, provider.remove)

    async def _confirm(
        self,
        model: type[Base],
        object_id: uuid.UUID,
        provider: str,
        external_id: str,
        *,
        version: int | None,
        job: JobType,
    ) -> Outcome:
        """Record a finished push only if it still describes the object (see module docstring)."""
        object_type = ObjectType.MEMORY if model is Memory else ObjectType.KB_ENTRY
        columns: Any = model
        async with self._sessions() as s, s.begin():
            obj: Any = await s.scalar(
                select(model).where(columns.id == object_id).with_for_update()
            )
            if obj is None or not _effective(obj):
                return "retired"
            if version is not None and obj.version != version:
                # still pending, but the engine holds a copy now: record its id so the retry
                # replaces that copy instead of creating a second one nothing would remove
                await s.execute(
                    update(ExternalRef)
                    .where(*_ref_key(object_type, object_id, provider))
                    .values(external_id=external_id)
                )
                key = "memory_id" if model is Memory else "entry_id"
                enqueue(s, obj.namespace_id, job, {key: str(object_id)})
                return "outdated"
            await s.execute(
                update(ExternalRef)
                .where(*_ref_key(object_type, object_id, provider))
                .values(_synced(external_id, version or 1))
            )
            return "synced"

    async def _retired(
        self, object_type: ObjectType, object_id: uuid.UUID, provider: str, external_id: str
    ) -> None:
        await self._update(
            object_type,
            object_id,
            provider,
            {"external_id": external_id, "sync_status": SyncStatus.DELETED, "last_error": None},
        )

    # external_refs bookkeeping

    @staticmethod
    async def _external_id(
        s: AsyncSession, object_type: ObjectType, object_id: uuid.UUID, provider: str
    ) -> str | None:
        return await s.scalar(
            select(ExternalRef.external_id).where(*_ref_key(object_type, object_id, provider))
        )

    async def _set(
        self,
        object_type: ObjectType,
        object_id: uuid.UUID,
        provider: str,
        *,
        synced: str | None = None,
        version: int | None = None,
        status: SyncStatus | None = None,
    ) -> None:
        values: dict[str, Any] = {"last_error": None}
        if synced is not None:
            values = _synced(synced, version)
        if status is not None:
            values["sync_status"] = status
        await self._update(object_type, object_id, provider, values)

    @asynccontextmanager
    async def _recording(
        self, object_type: ObjectType, object_id: uuid.UUID, provider: str
    ) -> AsyncIterator[None]:
        """Run a provider call; on failure mark the ref failed (with the error) and re-raise so
        the job backs off and retries."""
        try:
            yield
        except Exception as exc:
            await self._update(
                object_type,
                object_id,
                provider,
                {
                    "sync_status": SyncStatus.FAILED,
                    "last_error": describe_error(exc),
                    "attempts": ExternalRef.attempts + 1,
                },
            )
            raise

    async def _update(
        self, object_type: ObjectType, object_id: uuid.UUID, provider: str, values: dict[str, Any]
    ) -> None:
        async with self._sessions() as s, s.begin():
            result = await s.execute(
                update(ExternalRef)
                .where(*_ref_key(object_type, object_id, provider))
                .values(values)
            )
            if result.rowcount != 1:  # type: ignore[attr-defined]
                raise RuntimeError(
                    f"no external_refs row for {object_type.value} {object_id} at {provider}; "
                    "it must be opened by GovernanceService.apply"
                )


def _synced(external_id: str, version: int | None) -> dict[str, Any]:
    """An external_refs update: the provider holds `version` under `external_id`."""
    return {
        "external_id": external_id,
        "sync_status": SyncStatus.SYNCED,
        "synced_version": version,
        "last_synced_at": utcnow(),
        "last_error": None,
    }


def _effective(obj: Any) -> bool:
    if isinstance(obj, Memory):
        return obj.status == MemoryStatus.ACTIVE
    return obj.status in EFFECTIVE_KB_STATUSES
