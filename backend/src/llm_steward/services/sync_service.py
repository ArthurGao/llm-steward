"""Syncer: consumes outbox jobs and pushes effective objects to the providers.

GovernanceService opened each object's `external_refs` row in the write transaction; the Syncer only
records outcomes on it. Every handler is idempotent (a job may run twice after a lost lease): it
re-reads the object, skips work that no longer applies, and keys provider writes by the steward id.

Provider calls happen outside any database transaction, so the object can change while one is in
flight. An upsert therefore confirms its result under a row lock on the object (the same lock
GovernanceService takes to change it): still effective at the same version → synced; retired in
the meantime → the push is undone; edited in the meantime → another upsert is queued. A late
upsert can never leave retired or outdated content in a provider.
"""

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.models import ExternalRef, KbCollection, KbEntry, Memory
from llm_steward.models.base import Base, utcnow
from llm_steward.models.enums import (
    EFFECTIVE_KB_STATUSES,
    JobType,
    MemoryStatus,
    ObjectType,
    SyncStatus,
)
from llm_steward.providers.base import KbCollectionRef, KbEntryDoc, Message, Providers, Scope
from llm_steward.services.job_queue import ClaimedJob, describe_error, enqueue

Outcome = Literal["synced", "retired", "outdated"]


def _ref_key(object_type: ObjectType, object_id: uuid.UUID, provider: str) -> Any:
    return (
        ExternalRef.object_type == object_type,
        ExternalRef.object_id == object_id,
        ExternalRef.provider == provider,
    )


class SyncService:
    JOB_TYPES = (JobType.MEMORY_UPSERT, JobType.MEMORY_REMOVE, JobType.KB_UPSERT, JobType.KB_REMOVE)

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
        async with self._sessions() as s:
            external_id = await self._external_id(s, ObjectType.MEMORY, memory_id, provider.name)
        if external_id is not None:  # None: it never reached the provider
            async with self._recording(ObjectType.MEMORY, memory_id, provider.name):
                await provider.delete(external_id)
        await self._set(ObjectType.MEMORY, memory_id, provider.name, status=SyncStatus.DELETED)

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
        doc = KbEntryDoc(
            entry.id, entry.namespace_id, entry.title, entry.content, entry.version, entry.tags
        )
        async with self._recording(ObjectType.KB_ENTRY, entry_id, provider.name):
            if collection_ext is None:  # first entry of this collection: create it once
                collection_ext = await provider.ensure_collection(
                    KbCollectionRef(collection.id, collection.namespace_id, collection.name)
                )
                await self._set(
                    ObjectType.KB_COLLECTION, collection.id, provider.name, synced=collection_ext
                )
            external_id = await provider.upsert_entry(doc, collection_ext)
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
            external_id = await self._external_id(s, ObjectType.KB_ENTRY, entry_id, provider.name)
        if external_id is not None:
            async with self._recording(ObjectType.KB_ENTRY, entry_id, provider.name):
                await provider.remove(external_id)
        await self._set(ObjectType.KB_ENTRY, entry_id, provider.name, status=SyncStatus.DELETED)

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
                key = "memory_id" if model is Memory else "entry_id"
                enqueue(s, obj.namespace_id, job, {key: str(object_id)})
                return "outdated"
            await s.execute(
                update(ExternalRef)
                .where(*_ref_key(object_type, object_id, provider))
                .values(
                    external_id=external_id,
                    sync_status=SyncStatus.SYNCED,
                    synced_version=version or 1,
                    last_synced_at=utcnow(),
                    last_error=None,
                )
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
            values |= {"external_id": synced, "sync_status": SyncStatus.SYNCED}
            values |= {"last_synced_at": utcnow(), "synced_version": version}
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


def _effective(obj: Any) -> bool:
    if isinstance(obj, Memory):
        return obj.status == MemoryStatus.ACTIVE
    return obj.status in EFFECTIVE_KB_STATUSES
