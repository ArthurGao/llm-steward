"""The single write path (AGENTS.md rule 1).

`GovernanceService.apply(op, actor)` writes the object, a `change_log` row and any outbox job in ONE
transaction. Only effective knowledge is ever queued for a provider (rule 3): drafts, candidates and
rejected entries never are.
"""

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import Conflict, NotFound
from llm_steward.models import Candidate, ChangeLog, ExternalRef, KbCollection, KbEntry, Memory
from llm_steward.models.base import Base, utcnow
from llm_steward.models.enums import (
    EFFECTIVE_KB_STATUSES,
    ActorType,
    AuthorType,
    CandidateOp,
    CandidateOrigin,
    CandidateStage,
    CandidateStatus,
    CandidateTarget,
    ChangeOp,
    JobType,
    KbOrigin,
    KbStatus,
    MemoryOrigin,
    MemoryStatus,
    ObjectType,
    RiskLevel,
    SyncStatus,
)
from llm_steward.services.job_queue import enqueue

# Allowed state changes (design §5). Anything else is a 409.
KB_TRANSITIONS: dict[KbStatus, frozenset[KbStatus]] = {
    KbStatus.DRAFT: frozenset({KbStatus.ACTIVE, KbStatus.VERIFIED, KbStatus.REJECTED}),
    KbStatus.ACTIVE: frozenset({KbStatus.VERIFIED, KbStatus.DEPRECATED}),
    KbStatus.VERIFIED: frozenset({KbStatus.DEPRECATED}),
    KbStatus.DEPRECATED: frozenset(),
    KbStatus.REJECTED: frozenset(),
}
MEMORY_TRANSITIONS: dict[MemoryStatus, frozenset[MemoryStatus]] = {
    MemoryStatus.ACTIVE: frozenset({MemoryStatus.SUPERSEDED, MemoryStatus.DELETED}),
    MemoryStatus.SUPERSEDED: frozenset({MemoryStatus.DELETED}),
    MemoryStatus.DELETED: frozenset(),
}
CANDIDATE_TRANSITIONS: dict[CandidateStatus, frozenset[CandidateStatus]] = {
    CandidateStatus.PENDING: frozenset(
        {CandidateStatus.ACCEPTED, CandidateStatus.REJECTED, CandidateStatus.EXPIRED}
    ),
    CandidateStatus.ACCEPTED: frozenset(),
    CandidateStatus.REJECTED: frozenset(),
    CandidateStatus.EXPIRED: frozenset(),
}


@dataclass(frozen=True)
class Actor:
    type: ActorType
    id: str | None = None


SYSTEM = Actor(ActorType.SYSTEM)  # llm-steward's own writes (maintenance jobs)


# --- operations -------------------------------------------------------------------------------


@dataclass(frozen=True)
class CreateMemory:
    namespace_id: uuid.UUID
    content: str
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    memory_type: str = "semantic"
    origin: MemoryOrigin = MemoryOrigin.DIRECT
    pinned: bool = False
    importance: float = 0.5
    confidence: float = 0.8
    tags: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    source_episode_id: uuid.UUID | None = None


@dataclass(frozen=True)
class DeleteMemory:
    namespace_id: uuid.UUID
    memory_id: uuid.UUID
    reason: str | None = None


@dataclass(frozen=True)
class CreateCollection:
    namespace_id: uuid.UUID
    name: str
    default_risk: RiskLevel = RiskLevel.MEDIUM


@dataclass(frozen=True)
class CreateKbEntry:
    namespace_id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    content: str
    risk_level: RiskLevel
    origin: KbOrigin
    author_type: AuthorType
    status: KbStatus = KbStatus.DRAFT
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class UpdateKbEntry:
    namespace_id: uuid.UUID
    entry_id: uuid.UUID
    title: str | None = None
    content: str | None = None
    reason: str | None = None


@dataclass(frozen=True)
class TransitionKbEntry:
    namespace_id: uuid.UUID
    entry_id: uuid.UUID
    to: KbStatus
    reason: str | None = None


@dataclass(frozen=True)
class CreateCandidate:
    namespace_id: uuid.UUID
    target_type: CandidateTarget
    op: CandidateOp
    draft: dict[str, Any]
    risk_level: RiskLevel
    origin: CandidateOrigin
    stage: CandidateStage
    target_id: uuid.UUID | None = None
    reason: str | None = None
    requested_for_user: str | None = None


@dataclass(frozen=True)
class DecideCandidate:
    namespace_id: uuid.UUID
    candidate_id: uuid.UUID
    to: CandidateStatus
    note: str | None = None


Operation = (
    CreateMemory
    | DeleteMemory
    | CreateCollection
    | CreateKbEntry
    | UpdateKbEntry
    | TransitionKbEntry
    | CreateCandidate
    | DecideCandidate
)


# --- helpers ----------------------------------------------------------------------------------


def content_hash(*parts: str) -> str:
    return hashlib.sha256("\x1f".join(parts).encode()).hexdigest()


def _json_safe(value: Any) -> Any:
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    return value


def snapshot(obj: Base) -> dict[str, Any]:
    """Column values of a row, JSON-safe, for `change_log.before/after`."""
    return {attr.key: _json_safe(getattr(obj, attr.key)) for attr in obj.__mapper__.column_attrs}


def _check(transitions: dict[Any, frozenset[Any]], kind: str, current: Any, target: Any) -> None:
    if target not in transitions[current]:
        raise Conflict(f"{kind} cannot go from {current.value!r} to {target.value!r}")


# --- service ----------------------------------------------------------------------------------


class GovernanceService:
    """`memory_provider` / `kb_provider` name the providers whose `external_refs` rows are opened
    here, in the same transaction as the object and its outbox job (design §5 step 3)."""

    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], *, memory_provider: str, kb_provider: str
    ) -> None:
        self._sessions = sessions
        self._memory_provider = memory_provider
        self._kb_provider = kb_provider

    async def apply(self, op: Operation, actor: Actor) -> Any:
        async with self._sessions() as session, session.begin():
            obj = await self._dispatch(session, op, actor)
        return obj

    async def _dispatch(self, s: AsyncSession, op: Operation, actor: Actor) -> Any:
        match op:
            case CreateMemory():
                return await self._create_memory(s, op, actor)
            case DeleteMemory():
                return await self._delete_memory(s, op, actor)
            case CreateCollection():
                return await self._create_collection(s, op, actor)
            case CreateKbEntry():
                return await self._create_kb_entry(s, op, actor)
            case UpdateKbEntry():
                return await self._update_kb_entry(s, op, actor)
            case TransitionKbEntry():
                return await self._transition_kb_entry(s, op, actor)
            case CreateCandidate():
                return await self._create_candidate(s, op, actor)
            case DecideCandidate():
                return await self._decide_candidate(s, op, actor)

    # memories

    async def _create_memory(self, s: AsyncSession, op: CreateMemory, actor: Actor) -> Memory:
        memory = Memory(
            namespace_id=op.namespace_id,
            user_id=op.user_id,
            agent_id=op.agent_id,
            session_id=op.session_id,
            memory_type=op.memory_type,
            content=op.content,
            status=MemoryStatus.ACTIVE,
            origin=op.origin,
            pinned=op.pinned,
            importance=op.importance,
            confidence=op.confidence,
            valid_from=utcnow(),
            source_episode_id=op.source_episode_id,
            content_hash=content_hash(op.content),
            tags=list(op.tags),
            meta=dict(op.meta),
        )
        s.add(memory)
        await s.flush()
        self._log(s, ObjectType.MEMORY, memory, ChangeOp.CREATE, None, actor)
        await _mark_ref(s, ObjectType.MEMORY, memory, self._memory_provider, SyncStatus.PENDING)
        enqueue(s, op.namespace_id, JobType.MEMORY_UPSERT, {"memory_id": str(memory.id)})
        return memory

    async def _delete_memory(self, s: AsyncSession, op: DeleteMemory, actor: Actor) -> Memory:
        memory = await load_owned(s, Memory, op.namespace_id, op.memory_id, "memory")
        before = snapshot(memory)
        _check(MEMORY_TRANSITIONS, "memory", memory.status, MemoryStatus.DELETED)
        memory.status = MemoryStatus.DELETED
        memory.valid_to = utcnow()
        await s.flush()
        self._log(s, ObjectType.MEMORY, memory, ChangeOp.TRANSITION, before, actor, op.reason)
        await _mark_ref(s, ObjectType.MEMORY, memory, self._memory_provider, SyncStatus.DELETING)
        enqueue(s, op.namespace_id, JobType.MEMORY_REMOVE, {"memory_id": str(memory.id)})
        return memory

    # knowledge base

    async def _create_collection(
        self, s: AsyncSession, op: CreateCollection, actor: Actor
    ) -> KbCollection:
        collection = KbCollection(
            namespace_id=op.namespace_id, name=op.name, default_risk=op.default_risk
        )
        s.add(collection)
        await s.flush()
        self._log(s, ObjectType.KB_COLLECTION, collection, ChangeOp.CREATE, None, actor)
        await _mark_ref(
            s, ObjectType.KB_COLLECTION, collection, self._kb_provider, SyncStatus.PENDING
        )
        return collection

    async def _create_kb_entry(self, s: AsyncSession, op: CreateKbEntry, actor: Actor) -> KbEntry:
        await load_owned(s, KbCollection, op.namespace_id, op.collection_id, "collection")
        if op.status not in (KbStatus.DRAFT, *EFFECTIVE_KB_STATUSES):
            raise Conflict(f"a KB entry cannot be created as {op.status.value!r}")
        now = utcnow()
        entry = KbEntry(
            namespace_id=op.namespace_id,
            collection_id=op.collection_id,
            title=op.title,
            content=op.content,
            status=op.status,
            risk_level=op.risk_level,
            origin=op.origin,
            author_type=op.author_type,
            source_refs=list(op.source_refs),
            created_by=actor.id,
            valid_from=now,
            tags=list(op.tags),
            content_hash=content_hash(op.title, op.content),
        )
        if op.status == KbStatus.VERIFIED:
            entry.verified_by, entry.verified_at = actor.id, now
        s.add(entry)
        await s.flush()
        self._log(s, ObjectType.KB_ENTRY, entry, ChangeOp.CREATE, None, actor)
        if entry.status in EFFECTIVE_KB_STATUSES:
            await self._queue_kb(s, entry, JobType.KB_UPSERT)
        return entry

    async def _update_kb_entry(self, s: AsyncSession, op: UpdateKbEntry, actor: Actor) -> KbEntry:
        entry = await load_owned(s, KbEntry, op.namespace_id, op.entry_id, "KB entry")
        if entry.status not in (KbStatus.DRAFT, *EFFECTIVE_KB_STATUSES):
            raise Conflict(f"a {entry.status.value!r} KB entry cannot be edited")
        before = snapshot(entry)
        entry.title = op.title if op.title is not None else entry.title
        entry.content = op.content if op.content is not None else entry.content
        new_hash = content_hash(entry.title, entry.content)
        if new_hash == entry.content_hash:
            return entry  # nothing changed: no new version, no log, no sync
        entry.content_hash = new_hash
        entry.version += 1
        await s.flush()
        self._log(s, ObjectType.KB_ENTRY, entry, ChangeOp.UPDATE, before, actor, op.reason)
        if entry.status in EFFECTIVE_KB_STATUSES:
            await self._queue_kb(s, entry, JobType.KB_UPSERT)
        return entry

    async def _transition_kb_entry(
        self, s: AsyncSession, op: TransitionKbEntry, actor: Actor
    ) -> KbEntry:
        entry = await load_owned(s, KbEntry, op.namespace_id, op.entry_id, "KB entry")
        before = snapshot(entry)
        was_effective = entry.status in EFFECTIVE_KB_STATUSES
        _check(KB_TRANSITIONS, "KB entry", entry.status, op.to)
        now = utcnow()
        entry.status = op.to
        if op.to == KbStatus.VERIFIED:
            entry.verified_by, entry.verified_at = actor.id, now
        if op.to in (KbStatus.DEPRECATED, KbStatus.REJECTED):
            entry.valid_to = now
        await s.flush()
        self._log(s, ObjectType.KB_ENTRY, entry, ChangeOp.TRANSITION, before, actor, op.reason)
        if op.to in EFFECTIVE_KB_STATUSES:
            await self._queue_kb(s, entry, JobType.KB_UPSERT)
        elif was_effective:
            await self._queue_kb(s, entry, JobType.KB_REMOVE)
        return entry

    # candidates (state only in P1; materialising an accepted candidate arrives in P3)

    async def _create_candidate(
        self, s: AsyncSession, op: CreateCandidate, actor: Actor
    ) -> Candidate:
        candidate = Candidate(
            namespace_id=op.namespace_id,
            target_type=op.target_type,
            op=op.op,
            target_id=op.target_id,
            draft=dict(op.draft),
            reason=op.reason,
            risk_level=op.risk_level,
            origin=op.origin,
            stage=op.stage,
            status=CandidateStatus.PENDING,
            requested_for_user=op.requested_for_user,
        )
        s.add(candidate)
        await s.flush()
        self._log(s, ObjectType.CANDIDATE, candidate, ChangeOp.CREATE, None, actor)
        return candidate

    async def _decide_candidate(
        self, s: AsyncSession, op: DecideCandidate, actor: Actor
    ) -> Candidate:
        candidate = await load_owned(s, Candidate, op.namespace_id, op.candidate_id, "candidate")
        before = snapshot(candidate)
        _check(CANDIDATE_TRANSITIONS, "candidate", candidate.status, op.to)
        candidate.status = op.to
        candidate.decided_by, candidate.decided_at = actor.id, utcnow()
        candidate.decision_note = op.note
        await s.flush()
        self._log(s, ObjectType.CANDIDATE, candidate, ChangeOp.TRANSITION, before, actor, op.note)
        return candidate

    async def _queue_kb(self, s: AsyncSession, entry: KbEntry, job: JobType) -> None:
        status = SyncStatus.PENDING if job == JobType.KB_UPSERT else SyncStatus.DELETING
        await _mark_ref(s, ObjectType.KB_ENTRY, entry, self._kb_provider, status)
        enqueue(s, entry.namespace_id, job, {"entry_id": str(entry.id)})

    # change log

    def _log(
        self,
        s: AsyncSession,
        object_type: ObjectType,
        obj: Any,
        change: ChangeOp,
        before: dict[str, Any] | None,
        actor: Actor,
        reason: str | None = None,
    ) -> None:
        s.add(
            ChangeLog(
                namespace_id=obj.namespace_id,
                object_type=object_type,
                object_id=obj.id,
                op=change,
                before=before,
                after=snapshot(obj),
                actor_type=actor.type,
                actor_id=actor.id,
                reason=reason,
            )
        )


async def _mark_ref(
    s: AsyncSession, object_type: ObjectType, obj: Any, provider: str, status: SyncStatus
) -> None:
    """Open (or re-open) the object's `external_refs` row; the Syncer records the outcome on it."""
    ref = await s.scalar(
        select(ExternalRef).where(
            ExternalRef.object_type == object_type,
            ExternalRef.object_id == obj.id,
            ExternalRef.provider == provider,
        )
    )
    if ref is None:
        s.add(
            ExternalRef(
                namespace_id=obj.namespace_id,
                object_type=object_type,
                object_id=obj.id,
                provider=provider,
                sync_status=status,
            )
        )
    else:
        ref.sync_status = status


async def load_owned[T: Base](
    s: AsyncSession,
    model: type[T],
    namespace_id: uuid.UUID,
    object_id: uuid.UUID,
    kind: str,
    *,
    lock: bool = True,
) -> T:
    """Load by id within the namespace; another namespace's id is indistinguishable from none."""
    # `model` is any governed table; they all have `id` and `namespace_id` (not expressible on T).
    columns: Any = model
    query: Any = select(model).where(columns.id == object_id, columns.namespace_id == namespace_id)
    if lock:
        query = query.with_for_update()
    obj: T | None = await s.scalar(query)
    if obj is None:
        raise NotFound(f"{kind} {object_id} not found")
    return obj
