"""The single write path (AGENTS.md rule 1).

`GovernanceService.apply(op, actor)` writes the object, a `change_log` row and any outbox job in ONE
transaction. Only effective knowledge is ever queued for a provider (rule 3): drafts, candidates and
rejected entries never are.

KB writes from outside (the API, the pipeline) are *submitted* (design §5): the risk level and the
collection's review policy decide whether they go live, ask the end user, or wait for a reviewer.
Only a reviewer can make knowledge `verified`; an end user's confirmation never does.
"""

import hashlib
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Literal, cast

from sqlalchemy import delete, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import Conflict, Forbidden, NotFound, Unprocessable
from llm_steward.models import (
    Candidate,
    ChangeLog,
    Episode,
    ExternalRef,
    KbCollection,
    KbDocument,
    KbDocumentFile,
    KbEntry,
    Memory,
    Namespace,
    RetrievalLog,
)
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
    DocStatus,
    EpisodeKind,
    JobType,
    KbOrigin,
    KbStatus,
    MemoryOrigin,
    MemoryStatus,
    ObjectType,
    RiskLevel,
    SyncStatus,
)
from llm_steward.namespace_config import NamespaceConfig
from llm_steward.namespace_config import load as load_config
from llm_steward.pipeline.risk import Assessment, RiskPolicy, assess, redact_pii
from llm_steward.providers.base import Scope
from llm_steward.services.job_queue import enqueue
from llm_steward.services.scoping import exact

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
    """Who acts (recorded in change_log), and whether they may review: an admin, or an API key
    with the `review` scope, which still acts as the host app (P5: an admin's reviewer role)."""

    type: ActorType
    id: str | None = None
    can_review: bool = False

    @property
    def is_reviewer(self) -> bool:
        return self.type == ActorType.ADMIN or self.can_review


SYSTEM = Actor(ActorType.SYSTEM)  # llm-steward's own writes (maintenance jobs)

# Review routes (design §5): live at once, ask the end user, or wait for a reviewer. A collection's
# `review_policy` ({"low": route, "medium": route}) may change low and medium; high always waits.
Route = Literal["auto", "user", "review"]
ROUTES: tuple[Route, ...] = ("auto", "user", "review")
DEFAULT_ROUTES: dict[RiskLevel, Route] = {
    RiskLevel.LOW: "auto",
    RiskLevel.MEDIUM: "user",
    RiskLevel.HIGH: "review",
}
RISK_ORDER = (RiskLevel.LOW, RiskLevel.MEDIUM, RiskLevel.HIGH)
STAGES: dict[Route, CandidateStage] = {
    "user": CandidateStage.AWAITING_USER,
    "review": CandidateStage.AWAITING_REVIEW,
}
REMOVALS = frozenset({JobType.KB_REMOVE, JobType.DOC_REMOVE})
EDITABLE = frozenset({"title", "content"})  # what a decision may change in a draft
CANDIDATE_TTL = timedelta(days=7)  # unanswered cards expire (design §5)


def max_risk(*levels: RiskLevel) -> RiskLevel:
    return max(levels, key=RISK_ORDER.index)


def _require_reviewer(actor: Actor, what: str) -> None:
    if not actor.is_reviewer:
        raise Forbidden(f"only a reviewer can {what}")


def _require_reviewer_or_system(actor: Actor, what: str) -> None:
    """Taking knowledge out is a reviewer's decision, or llm-steward's own (maintenance)."""
    if actor.type != ActorType.SYSTEM:
        _require_reviewer(actor, what)


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
    # Profile memories: one current value per (scope, key); a new value supersedes the old one.
    key: str | None = None


@dataclass(frozen=True)
class RecordProviderMemory:
    """A memory the provider produced itself (e.g. mem0 extraction): mirror it as already synced."""

    namespace_id: uuid.UUID
    external_id: str
    content: str
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None
    source_episode_id: uuid.UUID | None = None


@dataclass(frozen=True)
class SupersedeMemory:
    """Invalidate, never delete (design §2 #3): the old memory stays queryable with `as_of` and
    stays in the provider; `by` is the memory that replaces it, if any."""

    namespace_id: uuid.UUID
    memory_id: uuid.UUID
    by: uuid.UUID | None = None
    reason: str | None = None


@dataclass(frozen=True)
class PatchMemory:
    """Metadata only; new content is a new memory that supersedes this one."""

    namespace_id: uuid.UUID
    memory_id: uuid.UUID
    pinned: bool | None = None
    importance: float | None = None
    confidence: float | None = None
    tags: list[str] | None = None
    meta: dict[str, Any] | None = None
    owner: str | None = None  # a user token's user: only their own memory


@dataclass(frozen=True)
class CreateEpisode:
    namespace_id: uuid.UUID
    kind: EpisodeKind
    messages: list[dict[str, str]]
    user_id: str | None = None
    agent_id: str | None = None
    session_id: str | None = None


@dataclass(frozen=True)
class DeleteMemory:
    namespace_id: uuid.UUID
    memory_id: uuid.UUID
    reason: str | None = None
    owner: str | None = None  # a user token's user: only their own memory


@dataclass(frozen=True)
class CreateCollection:
    namespace_id: uuid.UUID
    name: str
    default_risk: RiskLevel = RiskLevel.MEDIUM
    review_policy: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class UpdateCollection:
    namespace_id: uuid.UUID
    collection_id: uuid.UUID
    default_risk: RiskLevel | None = None
    review_policy: dict[str, str] | None = None


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
class SubmitKbEntry:
    """New knowledge from outside, routed by risk (design §5): a reviewer's goes live verified;
    otherwise low → active, medium → ask `requested_for_user` (active when there is none), high →
    the review queue. The text is screened here (rules, personal data); `risk_level` is the
    caller's estimate, which the rules and the collection's default can only raise."""

    namespace_id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    content: str
    risk_level: RiskLevel
    origin: KbOrigin
    author_type: AuthorType
    requested_for_user: str | None = None
    session_id: str | None = None  # the conversation a card belongs to
    source_refs: list[dict[str, Any]] = field(default_factory=list)
    tags: list[str] = field(default_factory=list)
    reason: str | None = None


@dataclass(frozen=True)
class ReviseKbEntry:
    """A new version of knowledge from outside: it applies at once, unless the entry is verified
    or the new text is high risk and the author is not a reviewer — then it waits in the review
    queue as an UPDATE candidate and the entry is unchanged."""

    namespace_id: uuid.UUID
    entry_id: uuid.UUID
    title: str | None = None
    content: str | None = None
    risk_level: RiskLevel = RiskLevel.LOW  # the caller's estimate; the rules can only raise it
    reason: str | None = None


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
class SubmitKbDocument:
    """An uploaded document, governed as a whole by its collection (design §4): risk = max(the
    caller's estimate, the rules on its title, the collection's default). A reviewer's upload is
    verified; high risk (or a `review` route) waits in the review queue and nothing reaches the
    engine; otherwise it is live at once (no end user is asked about a document). The content is
    not screened: it has not been parsed yet."""

    namespace_id: uuid.UUID
    collection_id: uuid.UUID
    title: str
    filename: str
    mime: str | None
    data: bytes = field(repr=False)
    content_hash: str  # sha256 of `data`, computed by the caller off the event loop
    risk_level: RiskLevel = RiskLevel.LOW


@dataclass(frozen=True)
class RetireKbDocument:
    """Take a document out (DELETE): a live one is deprecated (out of retrieval at once, out of
    the engine when the Syncer runs); one waiting for review is rejected, with its candidate."""

    namespace_id: uuid.UUID
    document_id: uuid.UUID
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
    session_id: str | None = None
    expires_at: datetime | None = None  # default: CANDIDATE_TTL from now


@dataclass(frozen=True)
class DecideCandidate:
    """Accept (materialise the draft), reject or expire. `draft` overrides fields of the draft
    (the user or reviewer edited it); `scope` is the end user's choice when accepting a card:
    `personal` keeps it as their memory, `team` sends it on as knowledge."""

    namespace_id: uuid.UUID
    candidate_id: uuid.UUID
    to: CandidateStatus
    note: str | None = None
    draft: dict[str, Any] | None = None
    scope: Literal["personal", "team"] = "team"
    risk_level: RiskLevel | None = None  # the decider's edit labelled (KB LLM): a floor


@dataclass(frozen=True)
class EraseUser:
    """Per-user erasure (design §7; the one exception to rule 6's "invalidate, never delete"):
    the user's memories and those memories' history, their episodes, the open cards asked of
    them or drafted from their episodes, and their retrieval logs are deleted. Engine copies are
    removed by the outbox (each job carries its engine id, since the rows and refs go now).
    Decided candidates and the team knowledge they became stay: they are the KB's audit trail."""

    namespace_id: uuid.UUID
    user_id: str


@dataclass(frozen=True)
class Erased:
    memories: int
    episodes: int
    candidates: int
    retrieval_logs: int


Operation = (
    CreateMemory
    | RecordProviderMemory
    | SupersedeMemory
    | PatchMemory
    | CreateEpisode
    | DeleteMemory
    | CreateCollection
    | UpdateCollection
    | CreateKbEntry
    | SubmitKbEntry
    | ReviseKbEntry
    | UpdateKbEntry
    | TransitionKbEntry
    | SubmitKbDocument
    | RetireKbDocument
    | CreateCandidate
    | DecideCandidate
    | EraseUser
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

    async def apply_all(self, ops: list[Operation], actor: Actor) -> list[Any]:
        """Several operations in one transaction: all of them happen, or none."""
        async with self._sessions() as session, session.begin():
            return [await self._dispatch(session, op, actor) for op in ops]

    async def _dispatch(self, s: AsyncSession, op: Operation, actor: Actor) -> Any:
        match op:
            case CreateMemory():
                return await self._create_memory(s, op, actor)
            case RecordProviderMemory():
                return await self._record_provider_memory(s, op, actor)
            case SupersedeMemory():
                return await self._supersede_memory(s, op, actor)
            case PatchMemory():
                return await self._patch_memory(s, op, actor)
            case CreateEpisode():
                return await self._create_episode(s, op, actor)
            case DeleteMemory():
                return await self._delete_memory(s, op, actor)
            case EraseUser():
                return await self._erase_user(s, op, actor)
            case CreateCollection():
                return await self._create_collection(s, op, actor)
            case UpdateCollection():
                return await self._update_collection(s, op, actor)
            case CreateKbEntry():
                return await self._create_kb_entry(s, op, actor)
            case SubmitKbEntry():
                return await self._submit_kb_entry(s, op, actor)
            case ReviseKbEntry():
                return await self._revise_kb_entry(s, op, actor)
            case UpdateKbEntry():
                return await self._update_kb_entry(s, op, actor)
            case TransitionKbEntry():
                return await self._transition_kb_entry(s, op, actor)
            case SubmitKbDocument():
                return await self._submit_kb_document(s, op, actor)
            case RetireKbDocument():
                return await self._retire_kb_document(s, op, actor)
            case CreateCandidate():
                return await self._create_candidate(s, op, actor)
            case DecideCandidate():
                return await self._decide_candidate(s, op, actor)

    # memories

    async def _create_memory(self, s: AsyncSession, op: CreateMemory, actor: Actor) -> Memory:
        if op.key and op.session_id:
            raise Unprocessable(
                "a profile value (key) belongs to a user, not a session: drop session_id"
            )
        previous = await self._current_profile_value(s, op) if op.key else None
        if previous is not None:  # leave the active slot (unique index) before the new value
            before = await _retire(s, previous)
        memory = Memory(
            namespace_id=op.namespace_id,
            user_id=op.user_id,
            agent_id=op.agent_id,
            session_id=op.session_id,
            memory_type=op.memory_type,
            key=op.key,
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
        if previous is not None:
            reason = f"profile key {op.key!r} has a new value"
            self._superseded(s, previous, memory.id, before, actor, reason)
        await _mark_ref(s, ObjectType.MEMORY, memory, self._memory_provider, SyncStatus.PENDING)
        enqueue(s, op.namespace_id, JobType.MEMORY_UPSERT, {"memory_id": str(memory.id)})
        return memory

    @staticmethod
    async def _current_profile_value(s: AsyncSession, op: CreateMemory) -> Memory | None:
        scope = Scope(op.namespace_id, op.user_id, op.agent_id)
        return await s.scalar(
            select(Memory)
            .where(
                *exact(Memory, scope, session=False),
                Memory.key == op.key,
                Memory.status == MemoryStatus.ACTIVE,
            )
            .with_for_update()
        )

    async def _record_provider_memory(
        self, s: AsyncSession, op: RecordProviderMemory, actor: Actor
    ) -> Memory:
        memory = Memory(
            namespace_id=op.namespace_id,
            user_id=op.user_id,
            agent_id=op.agent_id,
            session_id=op.session_id,
            memory_type="semantic",
            content=op.content,
            status=MemoryStatus.ACTIVE,
            origin=MemoryOrigin.EXTRACTION,
            valid_from=utcnow(),
            source_episode_id=op.source_episode_id,
            content_hash=content_hash(op.content),
            tags=[],
            meta={},
        )
        s.add(memory)
        await s.flush()
        self._log(s, ObjectType.MEMORY, memory, ChangeOp.CREATE, None, actor)
        # the provider already holds it: the ref is synced from the start, no upsert job
        await _mark_ref(
            s, ObjectType.MEMORY, memory, self._memory_provider, SyncStatus.SYNCED, op.external_id
        )
        return memory

    async def _supersede_memory(self, s: AsyncSession, op: SupersedeMemory, actor: Actor) -> Memory:
        memory = await load_owned(s, Memory, op.namespace_id, op.memory_id, "memory")
        if op.by is not None:
            await load_owned(s, Memory, op.namespace_id, op.by, "memory", lock=False)
        before = await _retire(s, memory)
        self._superseded(s, memory, op.by, before, actor, op.reason)
        return memory

    def _superseded(
        self,
        s: AsyncSession,
        memory: Memory,
        by: uuid.UUID | None,
        before: dict[str, Any],
        actor: Actor,
        reason: str | None,
    ) -> None:
        """Second half of a supersede: name the successor (if any) and log the transition."""
        memory.superseded_by = by
        self._log(s, ObjectType.MEMORY, memory, ChangeOp.TRANSITION, before, actor, reason)

    async def _patch_memory(self, s: AsyncSession, op: PatchMemory, actor: Actor) -> Memory:
        memory = await load_owned(
            s, Memory, op.namespace_id, op.memory_id, "memory", owner=op.owner
        )
        if memory.status != MemoryStatus.ACTIVE:
            raise Conflict(f"a {memory.status.value!r} memory cannot be edited")
        before = snapshot(memory)
        for attr in ("pinned", "importance", "confidence", "tags", "meta"):
            value = getattr(op, attr)
            if value is not None:
                setattr(memory, attr, value)
        await s.flush()
        self._log(s, ObjectType.MEMORY, memory, ChangeOp.UPDATE, before, actor)
        return memory

    async def _create_episode(self, s: AsyncSession, op: CreateEpisode, actor: Actor) -> Episode:
        episode = Episode(
            namespace_id=op.namespace_id,
            kind=op.kind,
            user_id=op.user_id,
            agent_id=op.agent_id,
            session_id=op.session_id,
            content={"messages": list(op.messages)},
        )
        s.add(episode)
        await s.flush()
        self._log(s, ObjectType.EPISODE, episode, ChangeOp.CREATE, None, actor)
        return episode

    async def _delete_memory(self, s: AsyncSession, op: DeleteMemory, actor: Actor) -> Memory:
        memory = await load_owned(
            s, Memory, op.namespace_id, op.memory_id, "memory", owner=op.owner
        )
        before = snapshot(memory)
        _check(MEMORY_TRANSITIONS, "memory", memory.status, MemoryStatus.DELETED)
        memory.status = MemoryStatus.DELETED
        memory.valid_to = utcnow()
        await s.flush()
        self._log(s, ObjectType.MEMORY, memory, ChangeOp.TRANSITION, before, actor, op.reason)
        await _mark_ref(s, ObjectType.MEMORY, memory, self._memory_provider, SyncStatus.DELETING)
        enqueue(s, op.namespace_id, JobType.MEMORY_REMOVE, {"memory_id": str(memory.id)})
        return memory

    async def _erase_user(self, s: AsyncSession, op: EraseUser, actor: Actor) -> Erased:
        if actor.type == ActorType.END_USER:
            raise Forbidden("erasure is requested by the host app, not from a user token")
        ns, user = op.namespace_id, op.user_id
        # Subqueries, not id lists: a heavy user's ids would exceed SQLite's bound-parameter cap.
        mine = (Memory.namespace_id == ns, Memory.user_id == user)
        memory_ids = select(Memory.id).where(*mine)
        episode_ids = select(Episode.id).where(Episode.namespace_id == ns, Episode.user_id == user)
        open_cards = (
            Candidate.namespace_id == ns,
            Candidate.status == CandidateStatus.PENDING,
            or_(Candidate.requested_for_user == user, Candidate.source_episode_id.in_(episode_ids)),
        )
        copies = await s.execute(
            delete(ExternalRef)
            .where(
                ExternalRef.namespace_id == ns,
                ExternalRef.object_type == ObjectType.MEMORY,
                ExternalRef.object_id.in_(memory_ids),
            )
            .returning(ExternalRef.object_id, ExternalRef.external_id, ExternalRef.sync_status)
        )
        for memory_id, external_id, status in copies.all():
            if external_id is not None and status != SyncStatus.DELETED:  # an engine copy
                payload = {"memory_id": str(memory_id), "external_id": external_id}
                enqueue(s, ns, JobType.MEMORY_REMOVE, payload)
        await s.execute(
            delete(ChangeLog).where(
                ChangeLog.namespace_id == ns,
                or_(
                    ChangeLog.object_id.in_(memory_ids),
                    ChangeLog.object_id.in_(episode_ids),
                    ChangeLog.object_id.in_(select(Candidate.id).where(*open_cards)),
                ),
            )
        )
        cards = await s.execute(delete(Candidate).where(*open_cards).returning(Candidate.id))
        # what stays must not point at what goes
        await s.execute(
            update(Memory)
            .where(
                Memory.namespace_id == ns,
                Memory.id.not_in(memory_ids),
                Memory.superseded_by.in_(memory_ids),
            )
            .values(superseded_by=None)
        )
        for model in (Memory, Candidate):
            columns: Any = model
            await s.execute(
                update(model)
                .where(columns.namespace_id == ns, columns.source_episode_id.in_(episode_ids))
                .values(source_episode_id=None)
            )
        memories = await s.execute(delete(Memory).where(*mine).returning(Memory.id))
        episodes = await s.execute(
            delete(Episode)
            .where(Episode.namespace_id == ns, Episode.user_id == user)
            .returning(Episode.id)
        )
        logs = await s.execute(
            delete(RetrievalLog)
            .where(
                RetrievalLog.namespace_id == ns,
                RetrievalLog.filters["user_id"].as_string() == user,
            )
            .returning(RetrievalLog.id)
        )
        return Erased(
            memories=len(memories.all()),
            episodes=len(episodes.all()),
            candidates=len(cards.all()),
            retrieval_logs=len(logs.all()),
        )

    # knowledge base

    async def _create_collection(
        self, s: AsyncSession, op: CreateCollection, actor: Actor
    ) -> KbCollection:
        collection = KbCollection(
            namespace_id=op.namespace_id,
            name=op.name,
            default_risk=op.default_risk,
            review_policy=_checked_policy(op.review_policy),
        )
        s.add(collection)
        await s.flush()
        self._log(s, ObjectType.KB_COLLECTION, collection, ChangeOp.CREATE, None, actor)
        await _mark_ref(
            s, ObjectType.KB_COLLECTION, collection, self._kb_provider, SyncStatus.PENDING
        )
        return collection

    async def _update_collection(
        self, s: AsyncSession, op: UpdateCollection, actor: Actor
    ) -> KbCollection:
        collection = await load_owned(
            s, KbCollection, op.namespace_id, op.collection_id, "collection"
        )
        before = snapshot(collection)
        if op.default_risk is not None:
            collection.default_risk = op.default_risk
        if op.review_policy is not None:
            collection.review_policy = _checked_policy(op.review_policy)
        await s.flush()
        self._log(s, ObjectType.KB_COLLECTION, collection, ChangeOp.UPDATE, before, actor)
        return collection

    async def _create_kb_entry(self, s: AsyncSession, op: CreateKbEntry, actor: Actor) -> KbEntry:
        # scopes the collection to the namespace; no row lock (the FK keeps it from going away)
        await load_owned(
            s, KbCollection, op.namespace_id, op.collection_id, "collection", lock=False
        )
        if op.status not in (KbStatus.DRAFT, *EFFECTIVE_KB_STATUSES):
            raise Conflict(f"a KB entry cannot be created as {op.status.value!r}")
        if op.status == KbStatus.VERIFIED:
            _require_reviewer(actor, "create verified knowledge")
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

    async def _create_from_draft(
        self,
        s: AsyncSession,
        namespace_id: uuid.UUID,
        draft: dict[str, Any],
        risk: RiskLevel,
        status: KbStatus,
        actor: Actor,
        extra_refs: tuple[dict[str, Any], ...] = (),
    ) -> KbEntry:
        """A draft (`_draft`, or a candidate's) becomes an entry: the one place the two meet."""
        create = CreateKbEntry(
            namespace_id=namespace_id,
            collection_id=uuid.UUID(draft["collection_id"]),
            title=draft["title"],
            content=draft["content"],
            risk_level=risk,
            origin=KbOrigin(draft.get("origin", KbOrigin.MANUAL)),
            author_type=AuthorType(draft.get("author_type", AuthorType.HUMAN)),
            status=status,
            source_refs=[*draft.get("source_refs", []), *extra_refs],
            tags=list(draft.get("tags", [])),
        )
        return await self._create_kb_entry(s, create, actor)

    async def _update_kb_entry(self, s: AsyncSession, op: UpdateKbEntry, actor: Actor) -> KbEntry:
        """Internal edits (no route builds this; outside text comes as `ReviseKbEntry`) are still
        screened: personal data is handled by policy, and high-risk text on live knowledge needs
        a reviewer, since this path has no review queue to wait in."""
        entry = await load_owned(s, KbEntry, op.namespace_id, op.entry_id, "KB entry")
        checked, title, content = await self._screen_edit(s, entry, op.title, op.content)
        live = entry.status in EFFECTIVE_KB_STATUSES
        if checked.level == RiskLevel.HIGH and live and not actor.is_reviewer:
            raise Forbidden("high-risk text on live knowledge needs a reviewer: submit a revision")
        reason = _reason(op.reason, checked)
        return await self._edit(s, entry, title, content, reason, actor, risk=checked.level)

    async def _screen_edit(
        self, s: AsyncSession, entry: KbEntry, title: str | None, content: str | None
    ) -> tuple[Assessment, str | None, str | None]:
        """Screen an edit as the whole new text (unchanged parts kept); returns the assessment
        and the screened title and content, each None where the edit leaves it alone."""
        checked = await self._screen(
            s,
            entry.namespace_id,
            title if title is not None else entry.title,
            content if content is not None else entry.content,
        )
        return (
            checked,
            checked.title if title is not None else None,
            checked.content if content is not None else None,
        )

    async def _edit(
        self,
        s: AsyncSession,
        entry: KbEntry,
        title: str | None,
        content: str | None,
        reason: str | None,
        actor: Actor,
        risk: RiskLevel | None = None,
    ) -> KbEntry:
        """A new version of `entry` (locked by the caller); nothing when the text is unchanged.
        `risk`, the new text's screened level, can only raise the entry's stored risk."""
        if entry.status not in (KbStatus.DRAFT, *EFFECTIVE_KB_STATUSES):
            raise Conflict(f"a {entry.status.value!r} KB entry cannot be edited")
        before = snapshot(entry)
        entry.title = title if title is not None else entry.title
        entry.content = content if content is not None else entry.content
        new_hash = content_hash(entry.title, entry.content)
        if new_hash == entry.content_hash:
            return entry  # nothing changed: no new version, no log, no sync
        entry.content_hash = new_hash
        entry.version += 1
        if risk is not None:
            entry.risk_level = max_risk(entry.risk_level, risk)
        await s.flush()
        self._log(s, ObjectType.KB_ENTRY, entry, ChangeOp.UPDATE, before, actor, reason)
        if entry.status in EFFECTIVE_KB_STATUSES:
            await self._queue_kb(s, entry, JobType.KB_UPSERT)
        return entry

    async def _transition_kb_entry(
        self, s: AsyncSession, op: TransitionKbEntry, actor: Actor
    ) -> KbEntry:
        entry = await load_owned(s, KbEntry, op.namespace_id, op.entry_id, "KB entry")
        return await self._transition(s, entry, op.to, op.reason, actor)

    async def _transition(
        self, s: AsyncSession, entry: KbEntry, to: KbStatus, reason: str | None, actor: Actor
    ) -> KbEntry:
        before = snapshot(entry)
        was_effective = entry.status in EFFECTIVE_KB_STATUSES
        _check(KB_TRANSITIONS, "KB entry", entry.status, to)
        if to == KbStatus.VERIFIED:
            _require_reviewer(actor, "verify knowledge")
        if to == KbStatus.DEPRECATED:
            _require_reviewer_or_system(actor, "deprecate knowledge")
        now = utcnow()
        entry.status = to
        if to == KbStatus.VERIFIED:
            entry.verified_by, entry.verified_at = actor.id, now
        if to in (KbStatus.DEPRECATED, KbStatus.REJECTED):
            entry.valid_to = now
        await s.flush()
        self._log(s, ObjectType.KB_ENTRY, entry, ChangeOp.TRANSITION, before, actor, reason)
        if to in EFFECTIVE_KB_STATUSES:
            await self._queue_kb(s, entry, JobType.KB_UPSERT)
        elif was_effective:
            await self._queue_kb(s, entry, JobType.KB_REMOVE)
        return entry

    async def _screen(
        self,
        s: AsyncSession,
        namespace_id: uuid.UUID,
        title: str,
        content: str,
        tags: tuple[str, ...] = (),
    ) -> Assessment:
        """Rule-based risk and PII (pipeline/risk.py) under the namespace's policy, in the write
        transaction: every outside write of knowledge text passes here, however it arrives."""
        policy = RiskPolicy.from_config(await namespace_config(s, namespace_id))
        return assess(title, content, policy, tags)

    async def _submit_kb_entry(
        self, s: AsyncSession, op: SubmitKbEntry, actor: Actor
    ) -> KbEntry | Candidate:
        collection = await load_owned(
            s, KbCollection, op.namespace_id, op.collection_id, "collection", lock=False
        )
        checked = await self._screen(s, op.namespace_id, op.title, op.content, tuple(op.tags))
        risk = max_risk(op.risk_level, checked.level, collection.default_risk)
        draft = _draft(op, checked)
        route = "auto" if actor.is_reviewer else _route(collection, risk)
        if route == "user" and op.requested_for_user is None:
            route = "auto"  # nobody to ask: medium goes live (design §5)
        if route == "auto" and op.author_type == AuthorType.AI:
            # A model rewrote untrusted text and labelled its own risk: a prompt injection could
            # make that "low". A person sees it first, the asked user or else a reviewer.
            route = "user" if op.requested_for_user else "review"
        if route == "auto":
            status = KbStatus.VERIFIED if actor.is_reviewer else KbStatus.ACTIVE
            return await self._create_from_draft(s, op.namespace_id, draft, risk, status, actor)
        candidate = CreateCandidate(
            namespace_id=op.namespace_id,
            target_type=CandidateTarget.KB_ENTRY,
            op=CandidateOp.ADD,
            draft=draft,
            risk_level=risk,
            origin=CANDIDATE_ORIGIN[op.origin],
            stage=STAGES[route],
            reason=_reason(op.reason, checked),
            requested_for_user=op.requested_for_user,
            session_id=op.session_id,
        )
        return await self._create_candidate(s, candidate, actor)

    async def _revise_kb_entry(
        self, s: AsyncSession, op: ReviseKbEntry, actor: Actor
    ) -> KbEntry | Candidate:
        entry = await load_owned(s, KbEntry, op.namespace_id, op.entry_id, "KB entry")
        collection = await load_owned(
            s, KbCollection, op.namespace_id, entry.collection_id, "collection", lock=False
        )
        checked, title, content = await self._screen_edit(s, entry, op.title, op.content)
        risk = max_risk(op.risk_level, checked.level, collection.default_risk)
        waits = entry.status == KbStatus.VERIFIED or _route(collection, risk) == "review"
        if actor.is_reviewer or not waits:
            reason = _reason(op.reason, checked)
            return await self._edit(s, entry, title, content, reason, actor, risk=risk)
        draft = {k: v for k, v in (("title", title), ("content", content)) if v is not None}
        candidate = CreateCandidate(
            namespace_id=op.namespace_id,
            target_type=CandidateTarget.KB_ENTRY,
            op=CandidateOp.UPDATE,
            target_id=entry.id,
            draft=draft,
            risk_level=risk,
            origin=CandidateOrigin.API,
            stage=CandidateStage.AWAITING_REVIEW,
            reason=_reason(op.reason, checked),
        )
        return await self._create_candidate(s, candidate, actor)

    # documents

    async def _submit_kb_document(
        self, s: AsyncSession, op: SubmitKbDocument, actor: Actor
    ) -> tuple[KbDocument, Candidate | None]:
        collection = await load_owned(
            s, KbCollection, op.namespace_id, op.collection_id, "collection", lock=False
        )
        digest = op.content_hash
        same = await s.scalar(
            select(KbDocument.id).where(
                KbDocument.namespace_id == op.namespace_id,
                KbDocument.collection_id == op.collection_id,
                KbDocument.content_hash == digest,
                KbDocument.state.in_((KbStatus.DRAFT, *EFFECTIVE_KB_STATUSES)),
            )
        )
        if same is not None:
            raise Conflict(f"this file is already document {same} in the collection")
        checked = await self._screen(s, op.namespace_id, op.title, "")
        risk = max_risk(op.risk_level, checked.level, collection.default_risk)
        if actor.is_reviewer:
            state = KbStatus.VERIFIED
        elif _route(collection, risk) == "review":
            state = KbStatus.DRAFT
        else:
            state = KbStatus.ACTIVE
        waits = state == KbStatus.DRAFT
        doc = KbDocument(
            namespace_id=op.namespace_id,
            collection_id=op.collection_id,
            title=checked.title,
            filename=op.filename,
            mime=op.mime,
            content_hash=digest,
            size_bytes=len(op.data),
            status=DocStatus.PENDING,
            risk_level=risk,
            state=state,
            created_by=actor.id,
        )
        s.add(doc)
        await s.flush()
        s.add(KbDocumentFile(document_id=doc.id, namespace_id=doc.namespace_id, data=op.data))
        reason = _reason(None, checked)
        self._log(s, ObjectType.KB_DOCUMENT, doc, ChangeOp.CREATE, None, actor, reason)
        if not waits:
            await self._queue_kb(s, doc, JobType.DOC_INGEST)
            return doc, None
        candidate = CreateCandidate(
            namespace_id=op.namespace_id,
            target_type=CandidateTarget.KB_DOCUMENT,
            op=CandidateOp.ADD,
            target_id=doc.id,
            draft={"collection_id": str(doc.collection_id), "title": doc.title},
            risk_level=risk,
            origin=CandidateOrigin.API,
            stage=CandidateStage.AWAITING_REVIEW,
            reason=reason,
        )
        return doc, await self._create_candidate(s, candidate, actor)

    async def _retire_kb_document(
        self, s: AsyncSession, op: RetireKbDocument, actor: Actor
    ) -> KbDocument:
        _require_reviewer_or_system(actor, "take a document out")
        doc = await load_owned(s, KbDocument, op.namespace_id, op.document_id, "document")
        if doc.state != KbStatus.DRAFT:
            return await self._transition_document(s, doc, KbStatus.DEPRECATED, op.reason, actor)
        waiting = await s.scalar(
            select(Candidate.id).where(
                Candidate.namespace_id == doc.namespace_id,
                Candidate.target_type == CandidateTarget.KB_DOCUMENT,
                Candidate.target_id == doc.id,
                Candidate.status == CandidateStatus.PENDING,
            )
        )
        if waiting is None:  # its candidate expired or was decided: reject the document itself
            return await self._transition_document(s, doc, KbStatus.REJECTED, op.reason, actor)
        decide = DecideCandidate(
            doc.namespace_id, waiting, CandidateStatus.REJECTED, note=op.reason or "deleted"
        )
        await self._decide_candidate(s, decide, actor)  # rejects the document with it
        return doc

    async def _transition_document(
        self, s: AsyncSession, doc: KbDocument, to: KbStatus, reason: str | None, actor: Actor
    ) -> KbDocument:
        """A document's governed state (the entries' state machine); going live queues the upload,
        leaving it queues the removal, and a document that is out drops its waiting bytes."""
        before = snapshot(doc)
        was_live = doc.state in EFFECTIVE_KB_STATUSES
        _check(KB_TRANSITIONS, "document", doc.state, to)
        if to == KbStatus.VERIFIED:
            _require_reviewer(actor, "verify a document")
        doc.state = to
        if to in (KbStatus.DEPRECATED, KbStatus.REJECTED):
            doc.valid_to = utcnow()
            await s.execute(delete(KbDocumentFile).where(KbDocumentFile.document_id == doc.id))
        await s.flush()
        self._log(s, ObjectType.KB_DOCUMENT, doc, ChangeOp.TRANSITION, before, actor, reason)
        if to in EFFECTIVE_KB_STATUSES and not was_live:
            await self._queue_kb(s, doc, JobType.DOC_INGEST)
        elif was_live and to not in EFFECTIVE_KB_STATUSES:
            await self._queue_kb(s, doc, JobType.DOC_REMOVE)
        return doc

    # candidates

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
            session_id=op.session_id,
            expires_at=op.expires_at or utcnow() + CANDIDATE_TTL,
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
        _check_decider(candidate, actor, op.to)
        note = redact_pii(op.note) if op.note else None  # free text: no personal data in history
        accepted = op.to == CandidateStatus.ACCEPTED
        if accepted and candidate.target_type != CandidateTarget.MEMORY:
            if candidate.expires_at is not None and candidate.expires_at <= utcnow():
                raise Conflict(f"candidate {candidate.id} expired at {candidate.expires_at}")
        if candidate.target_type == CandidateTarget.KB_DOCUMENT:
            if op.draft:
                raise Unprocessable("a document cannot be edited in a decision")
            doc = await load_owned(s, KbDocument, op.namespace_id, _target(candidate), "document")
            to = KbStatus.VERIFIED if accepted else KbStatus.REJECTED
            await self._transition_document(s, doc, to, note, actor)
        elif accepted and candidate.target_type == CandidateTarget.KB_ENTRY:
            draft, risk = await self._edited(s, candidate, op.draft)
            risk = max_risk(risk, op.risk_level or RiskLevel.LOW)
            by_user = candidate.stage == CandidateStage.AWAITING_USER and not actor.is_reviewer
            if by_user and op.scope == "personal":  # the user keeps it for themselves
                await self._create_memory(s, _memory_op(candidate, draft), actor)
            elif by_user and risk == RiskLevel.HIGH:  # useful is not correct: a reviewer decides
                candidate.stage = CandidateStage.AWAITING_REVIEW
                candidate.draft, candidate.risk_level = draft, risk
                await s.flush()
                self._log(s, ObjectType.CANDIDATE, candidate, ChangeOp.UPDATE, before, actor, note)
                return candidate
            else:
                status = KbStatus.ACTIVE if by_user else KbStatus.VERIFIED
                candidate.risk_level = risk
                entry = await self._materialise(s, candidate, draft, status, actor)
                candidate.target_id = entry.id  # an ADD now names what it created
        candidate.status = op.to
        candidate.decided_by, candidate.decided_at = actor.id, utcnow()
        candidate.decision_note = note
        await s.flush()
        self._log(s, ObjectType.CANDIDATE, candidate, ChangeOp.TRANSITION, before, actor, note)
        return candidate

    async def _edited(
        self, s: AsyncSession, candidate: Candidate, edits: dict[str, Any] | None
    ) -> tuple[dict[str, Any], RiskLevel]:
        """The draft with the decider's edits, screened again: an edit can add personal data or a
        high-risk subject, and must not slip past the rules a submission went through."""
        if not edits:
            return dict(candidate.draft), candidate.risk_level
        unknown = sorted(set(edits) - EDITABLE)
        if unknown:
            raise Unprocessable(
                f"a decision can only edit title and content, not {', '.join(unknown)}"
            )
        draft = {**candidate.draft, **edits}
        checked = await self._screen(
            s, candidate.namespace_id, draft.get("title", ""), draft.get("content", "")
        )
        for key, value in (("title", checked.title), ("content", checked.content)):
            if key in draft:
                draft[key] = value
        return draft, max_risk(candidate.risk_level, checked.level)

    async def _materialise(
        self,
        s: AsyncSession,
        candidate: Candidate,
        draft: dict[str, Any],
        status: KbStatus,
        actor: Actor,
    ) -> KbEntry:
        """An accepted KB candidate becomes knowledge with `status` (ACTIVE from an end user,
        VERIFIED from a reviewer), in the same transaction as the decision."""
        ns = candidate.namespace_id
        if candidate.op == CandidateOp.ADD:
            ref = {"type": "candidate", "id": str(candidate.id)}
            return await self._create_from_draft(
                s, ns, draft, candidate.risk_level, status, actor, (ref,)
            )
        entry = await load_owned(s, KbEntry, ns, _target(candidate), "KB entry")
        if candidate.op == CandidateOp.DELETE:
            return await self._transition(s, entry, KbStatus.DEPRECATED, None, actor)
        entry = await self._edit(s, entry, draft.get("title"), draft.get("content"), None, actor)
        if entry.status != status and status in KB_TRANSITIONS[entry.status]:
            entry = await self._transition(s, entry, status, None, actor)
        return entry

    async def expire_candidates(self) -> int:
        """Pending candidates past their expiry become `expired` (a daily worker tick), each in
        its own transaction: one decided meanwhile is skipped, not the whole sweep."""
        async with self._sessions() as s:
            due = list(
                await s.execute(
                    select(Candidate.namespace_id, Candidate.id).where(
                        Candidate.status == CandidateStatus.PENDING,
                        Candidate.expires_at <= utcnow(),
                    )
                )
            )
        expired = 0
        for namespace_id, candidate_id in due:
            op = DecideCandidate(
                namespace_id, candidate_id, CandidateStatus.EXPIRED, note="expired"
            )
            try:
                await self.apply(op, SYSTEM)
            except Conflict:
                continue  # decided between the scan and now
            expired += 1
        return expired

    async def _queue_kb(self, s: AsyncSession, obj: KbEntry | KbDocument, job: JobType) -> None:
        """Open the object's ref at the KB provider and queue its push (or removal)."""
        status = SyncStatus.DELETING if job in REMOVALS else SyncStatus.PENDING
        object_type, key = (
            (ObjectType.KB_DOCUMENT, "document_id")
            if isinstance(obj, KbDocument)
            else (ObjectType.KB_ENTRY, "entry_id")
        )
        await _mark_ref(s, object_type, obj, self._kb_provider, status)
        enqueue(s, obj.namespace_id, job, {key: str(obj.id)})

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
                # free text about a change: no personal data in history, whichever op carried it
                reason=redact_pii(reason) if reason else None,
            )
        )


async def _retire(s: AsyncSession, memory: Memory) -> dict[str, Any]:
    """First half of a supersede: leave the active state (and with it any unique active slot);
    returns the snapshot from before."""
    _check(MEMORY_TRANSITIONS, "memory", memory.status, MemoryStatus.SUPERSEDED)
    before = snapshot(memory)
    memory.status, memory.valid_to = MemoryStatus.SUPERSEDED, utcnow()
    await s.flush()
    return before


async def _mark_ref(
    s: AsyncSession,
    object_type: ObjectType,
    obj: Any,
    provider: str,
    status: SyncStatus,
    external_id: str | None = None,
) -> None:
    """Open (or re-open) the object's `external_refs` row; the Syncer records the outcome on it.
    With `external_id`, the provider already holds version 1 (status SYNCED)."""
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
                **(
                    {"external_id": external_id, "synced_version": 1, "last_synced_at": utcnow()}
                    if external_id
                    else {}
                ),
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
    owner: str | None = None,
) -> T:
    """Load by id within the namespace; another namespace's id is indistinguishable from none.
    `owner` (a user token's user): also only that user's own row, so another user's, or a shared
    one, is indistinguishable from none too."""
    # `model` is any governed table; they all have `id` and `namespace_id` (not expressible on T).
    columns: Any = model
    query: Any = select(model).where(columns.id == object_id, columns.namespace_id == namespace_id)
    if owner is not None:
        query = query.where(columns.user_id == owner)
    if lock:
        query = query.with_for_update()
    obj: T | None = await s.scalar(query)
    if obj is None:
        raise NotFound(f"{kind} {object_id} not found")
    return obj


CANDIDATE_ORIGIN: dict[KbOrigin, CandidateOrigin] = {
    KbOrigin.MANUAL: CandidateOrigin.API,
    KbOrigin.IMPORT: CandidateOrigin.API,
    KbOrigin.PROMOTED_MEMORY: CandidateOrigin.PROMOTE,
    KbOrigin.USER_SUGGESTION: CandidateOrigin.USER_SUGGESTION,
    KbOrigin.EXTRACTION: CandidateOrigin.EXTRACTION,
}


def _route(collection: KbCollection, risk: RiskLevel) -> Route:
    """The collection's route for `risk`; high always goes to review, whatever the policy says."""
    if risk == RiskLevel.HIGH:
        return "review"
    return cast(Route, (collection.review_policy or {}).get(risk.value, DEFAULT_ROUTES[risk]))


def _draft(op: SubmitKbEntry, checked: Assessment) -> dict[str, Any]:
    """A submission as a draft (JSON): what a candidate holds and `_create_from_draft` reads."""
    return {
        "collection_id": str(op.collection_id),
        "title": checked.title,
        "content": checked.content,
        "origin": op.origin.value,
        "author_type": op.author_type.value,
        "source_refs": list(op.source_refs),
        "tags": list(checked.tags),
    }


def _reason(given: str | None, checked: Assessment) -> str | None:
    """What is written about a change; a caller's or model's free text is redacted (PII)."""
    return "; ".join([*([redact_pii(given)] if given else []), *checked.reasons]) or None


def _memory_op(candidate: Candidate, draft: dict[str, Any]) -> CreateMemory:
    return CreateMemory(
        namespace_id=candidate.namespace_id,
        content=draft["content"],
        user_id=candidate.requested_for_user,
        source_episode_id=candidate.source_episode_id,
    )


def _target(candidate: Candidate) -> uuid.UUID:
    if candidate.target_id is None:
        raise Unprocessable(f"{candidate.op.value} candidate {candidate.id} names no target")
    return candidate.target_id


def _check_decider(candidate: Candidate, actor: Actor, to: CandidateStatus) -> None:
    """The review queue is a reviewer's; a card is the asked user's (or a reviewer's). Expiry is
    llm-steward's own (SYSTEM)."""
    if to == CandidateStatus.EXPIRED and actor.type == ActorType.SYSTEM:
        return
    if candidate.stage == CandidateStage.AWAITING_REVIEW:
        _require_reviewer(actor, "decide a candidate in the review queue")
    elif not actor.is_reviewer:
        asked = candidate.requested_for_user
        if actor.type != ActorType.END_USER or actor.id != asked:
            raise Forbidden(f"only the asked user {asked!r} (or a reviewer) can answer this card")


def _checked_policy(policy: dict[str, str]) -> dict[str, str]:
    """A collection may route low and medium risk; high always goes to review."""
    for risk, route in policy.items():
        if risk not in (RiskLevel.LOW, RiskLevel.MEDIUM) or route not in ROUTES:
            raise Unprocessable(
                f"review_policy {risk!r}: {route!r} — only low/medium can be routed, to one of "
                f"{', '.join(ROUTES)} (high always goes to review)"
            )
    return dict(policy)


async def namespace_config(s: AsyncSession, namespace_id: uuid.UUID) -> NamespaceConfig:
    """A namespace's config (design §8), validated; every default for one that has none."""
    namespace = await s.get(Namespace, namespace_id)
    return load_config(namespace.config if namespace else None)
