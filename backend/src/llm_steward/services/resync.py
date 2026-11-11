"""Full resync after an engine switch (design §11 P7b, `steward resync`): queue the usual Syncer
job for every effective object the configured engine does not hold at its current version, and
the removal of every copy it still holds of something retired meanwhile (rule 3). No governed
state, version or change_log row changes; refs and jobs are operational outbox, like an admin
retry. A document whose bytes are gone (dropped before they were kept) is reported for
re-upload, not queued.
"""

import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, and_, exists, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.models import ExternalRef, KbDocument, KbDocumentFile, KbEntry, Memory
from llm_steward.models.enums import (
    EFFECTIVE_KB_STATUSES,
    DocStatus,
    MemoryStatus,
    ObjectType,
    SyncStatus,
)
from llm_steward.services.job_queue import queue_removal, queue_sync


@dataclass(frozen=True)
class ResyncReport:
    entries: int = 0
    memories: int = 0
    documents: int = 0
    removed: int = 0  # copies of retired objects queued for removal
    documents_without_file: list[uuid.UUID] = field(default_factory=list)  # re-upload these


@dataclass(frozen=True)
class _Kind:
    model: Any
    object_type: ObjectType
    effective: Callable[[], ColumnElement[bool]]
    # what governance takes out of an engine (a superseded memory is left to the engine)
    retired: Callable[[], ColumnElement[bool]]
    versioned: bool = False  # a synced copy of an older version is stale


ENTRIES = _Kind(
    KbEntry,
    ObjectType.KB_ENTRY,
    lambda: KbEntry.status.in_(EFFECTIVE_KB_STATUSES),
    lambda: KbEntry.status.not_in(EFFECTIVE_KB_STATUSES),
    versioned=True,
)
DOCUMENTS = _Kind(
    KbDocument,
    ObjectType.KB_DOCUMENT,
    lambda: KbDocument.state.in_(EFFECTIVE_KB_STATUSES),
    lambda: KbDocument.state.not_in(EFFECTIVE_KB_STATUSES),
)
MEMORIES = _Kind(
    Memory,
    ObjectType.MEMORY,
    lambda: Memory.status == MemoryStatus.ACTIVE,
    lambda: Memory.status == MemoryStatus.DELETED,
)


async def resync(
    sessions: async_sessionmaker[AsyncSession],
    namespace_id: uuid.UUID,
    *,
    kb_provider: str,
    memory_provider: str,
    kb: bool = True,
    memory: bool = True,
) -> ResyncReport:
    """Queue what `kb_provider` / `memory_provider` (the configured engines' names) lack, in one
    transaction for the namespace."""
    counts = dict.fromkeys(ObjectType, 0)
    without_file: list[uuid.UUID] = []
    removed = 0
    kinds = [(k, kb_provider) for k in (ENTRIES, DOCUMENTS) if kb]
    kinds += [(MEMORIES, memory_provider)] if memory else []
    async with sessions() as s, s.begin():
        for kind, provider in kinds:
            objects = await _lacking(s, kind, namespace_id, provider)
            if kind is DOCUMENTS:
                held = await _with_file(s, [d.id for d in objects])
                without_file = [d.id for d in objects if d.id not in held and not d.fetch_pending]
                objects = [d for d in objects if d.id in held]  # fetching: its fetch ingests it
                for doc in objects:  # its parse status was the old engine's; this one starts
                    doc.status, doc.error = DocStatus.PENDING, None
            for obj in objects:
                await queue_sync(s, kind.object_type, obj, provider)
            counts[kind.object_type] = len(objects)
            for obj in (stale := await _still_held(s, kind, namespace_id, provider)):
                await queue_removal(s, kind.object_type, obj, provider)
            removed += len(stale)
    return ResyncReport(
        entries=counts[ObjectType.KB_ENTRY],
        memories=counts[ObjectType.MEMORY],
        documents=counts[ObjectType.KB_DOCUMENT],
        removed=removed,
        documents_without_file=without_file,
    )


async def _lacking(
    s: AsyncSession, kind: _Kind, namespace_id: uuid.UUID, provider: str
) -> list[Any]:
    """Effective objects the provider does not hold: no ref there, or a ref that is neither
    pending (already queued) nor synced (at the object's version, when versioned)."""
    model, ref = kind.model, ExternalRef
    synced = ref.sync_status == SyncStatus.SYNCED
    if kind.versioned:
        synced = and_(synced, ref.synced_version == model.version)
    has_it = exists().where(
        ref.object_type == kind.object_type,
        ref.object_id == model.id,
        ref.provider == provider,
        or_(ref.sync_status == SyncStatus.PENDING, synced),
    )
    query = select(model).where(model.namespace_id == namespace_id, kind.effective(), ~has_it)
    return list(await s.scalars(query.order_by(model.id)))


async def _still_held(
    s: AsyncSession, kind: _Kind, namespace_id: uuid.UUID, provider: str
) -> list[Any]:
    """Retired objects the provider may still hold a copy of (a synced or failed push there; a
    removal already queued is `deleting`)."""
    ref = ExternalRef
    held = exists().where(
        ref.object_type == kind.object_type,
        ref.object_id == kind.model.id,
        ref.provider == provider,
        ref.sync_status.in_((SyncStatus.SYNCED, SyncStatus.FAILED)),
    )
    query = select(kind.model).where(kind.model.namespace_id == namespace_id, kind.retired(), held)
    return list(await s.scalars(query.order_by(kind.model.id)))


async def _with_file(s: AsyncSession, ids: list[uuid.UUID]) -> set[uuid.UUID]:
    """Which of these documents still have their bytes (ids only: the bytes are not read)."""
    if not ids:
        return set()
    found = await s.scalars(
        select(KbDocumentFile.document_id).where(KbDocumentFile.document_id.in_(ids))
    )
    return set(found)
