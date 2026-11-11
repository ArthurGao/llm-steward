"""Knowledge: writes are submitted to GovernanceService, which screens them (rules, personal data)
and routes them by risk (design §5); reads come from the governance tables.

With the KB LLM on (`STEWARD_KB_LLM`), the LLM steps run here, outside any DB transaction: a risk
label on every submission (it can only raise the risk), drafting for promote and suggestions, and
worthiness scoring of new memories (the `kb.suggest` job, design §5 steps 5-6). The model proposes;
governance and the code here decide."""

import asyncio
import logging
import uuid
from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import NotFound, Unprocessable
from llm_steward.llm.client import LLM
from llm_steward.llm.roles import Provenance
from llm_steward.models import Candidate, KbCollection, KbEntry, Memory
from llm_steward.models.base import utcnow
from llm_steward.models.enums import (
    EFFECTIVE_KB_STATUSES,
    RESERVED_KEY_PREFIX,
    ActorType,
    AuthorType,
    CandidateStage,
    CandidateStatus,
    JobType,
    KbOrigin,
    KbStatus,
    MemoryStatus,
    RiskLevel,
)
from llm_steward.namespace_config import read_config
from llm_steward.pipeline.kb_llm import (
    DraftReply,
    draft_entry,
    label_risk,
    score_memories,
    worthy_ids,
)
from llm_steward.providers.base import Scope
from llm_steward.services.governance_service import (
    SYSTEM,
    Actor,
    CreateCollection,
    CreateMemory,
    DecideCandidate,
    FlagKbEntryForReview,
    GovernanceService,
    Operation,
    PatchMemory,
    ReviseKbEntry,
    SubmitKbEntry,
    load_owned,
    max_risk,
)
from llm_steward.services.job_queue import ClaimedJob, enqueue
from llm_steward.services.paging import page

log = logging.getLogger(__name__)

SCORED = "kb_scored"  # memory meta flag: scored for the KB once, never again
DRAFTS_AT_ONCE = 4  # concurrent kb.draft calls in one suggest job
# A user's "stop suggesting" (design §5, per user: extracted facts carry no finer category yet):
# a procedural memory under this profile key (one active per user), deleting it undoes it.
PROCEDURAL = "procedural"
STOP_KEY = f"{RESERVED_KEY_PREFIX}stop_suggestions"  # only llm-steward writes it
# Feedback-driven review (design §5 maintenance; v1 constants, a namespace setting later)
REVIEW_MIN_HITS = 5  # answers since the last review, at least
REVIEW_UNHELPFUL_PERCENT = 30  # of the ratings since then, unhelpful, at least
VERIFY_MIN_HELPFUL = 5  # helpful ratings, at least, to be worth verifying
VERIFY_HELPFUL_PERCENT = 80  # of all ratings, helpful, at least

type ReviewFilter = Literal["due", "verify"]


def _share_at_least(part: Any, whole: Any, percent: int) -> Any:
    """`part / whole >= percent%`, in integers (the same on SQLite and PostgreSQL)."""
    return part * 100 >= whole * percent


@dataclass(frozen=True)
class Submitted:
    """What a write became: live knowledge, or a candidate waiting for someone."""

    entry: KbEntry | None
    candidate: Candidate | None

    @classmethod
    def of(cls, obj: KbEntry | Candidate) -> "Submitted":
        return cls(obj, None) if isinstance(obj, KbEntry) else cls(None, obj)


@dataclass(frozen=True)
class EntryFilter:
    collection_id: uuid.UUID | None = None
    status: KbStatus | None = None
    # `due`: flagged for review (effective, its date reached); `verify`: active and well rated,
    # worth a reviewer's verification
    review: ReviewFilter | None = None


@dataclass(frozen=True)
class CandidateFilter:
    stage: CandidateStage | None = None
    status: CandidateStatus | None = CandidateStatus.PENDING
    user_id: str | None = None


class KbService:
    def __init__(
        self,
        sessions: async_sessionmaker[AsyncSession],
        governance: GovernanceService,
        llm: LLM | None = None,
    ) -> None:
        """`llm`: the KB roles (kb.risk, kb.worthiness, kb.draft), or None when they are off."""
        self._sessions = sessions
        self._governance = governance
        self._llm = llm

    def _drafting(self) -> LLM:
        if self._llm is None:
            raise Unprocessable(
                "drafting knowledge needs the KB LLM: set STEWARD_KB_LLM=true (roles kb.risk, "
                "kb.worthiness, kb.draft on the configured LLM endpoint)"
            )
        return self._llm

    async def submit(
        self,
        namespace_id: uuid.UUID,
        collection_id: uuid.UUID,
        title: str,
        content: str,
        *,
        actor: Actor,
        risk_level: RiskLevel = RiskLevel.LOW,
        user_id: str | None = None,
        session_id: str | None = None,
        tags: list[str] | None = None,
        origin: KbOrigin = KbOrigin.MANUAL,
        author_type: AuthorType = AuthorType.HUMAN,
        source_refs: list[dict[str, Any]] | None = None,
        reason: str | None = None,
        labelled: bool = False,
    ) -> Submitted:
        """`risk_level` is the caller's own estimate: the LLM label (when on) and the rules can
        only raise it. `labelled`: the risk already comes from a model (a draft labels itself)."""
        reasons = [reason] if reason else []
        if not labelled:
            risk_level, notes = await self._label(title, content, risk_level)
            reasons += notes
        op = SubmitKbEntry(
            namespace_id=namespace_id,
            collection_id=collection_id,
            title=title,
            content=content,
            risk_level=risk_level,
            origin=origin,
            author_type=author_type,
            requested_for_user=user_id,
            session_id=session_id,
            source_refs=list(source_refs or []),
            tags=list(tags or []),
            reason="; ".join(reasons) or None,
        )
        return Submitted.of(await self._governance.apply(op, actor))

    async def _label(
        self, title: str, content: str, floor: RiskLevel
    ) -> tuple[RiskLevel, list[str]]:
        """The KB LLM's risk label on text that changes (submit, revise, an edited draft): it can
        only raise `floor`. No call when the LLM is off or the risk is already high."""
        if self._llm is None or floor == RiskLevel.HIGH:
            return floor, []
        label = await label_risk(self._llm, title, content)
        return max_risk(floor, label.level), [f"{label.provenance.tag}: {label.reason}"]

    async def _drafted(
        self,
        namespace_id: uuid.UUID,
        collection_id: uuid.UUID,
        text: str,
        *,
        actor: Actor | None,
        origin: KbOrigin,
        user_id: str | None,
        session_id: str | None = None,
        source_refs: list[dict[str, Any]] | None = None,
    ) -> Submitted:
        """`text` rewritten as general knowledge by `kb.draft`, then submitted like any write.
        `actor` None: the drafting model itself (the pipeline's own suggestions)."""
        drafted = await draft_entry(self._drafting(), text)
        return await self._submit_draft(
            namespace_id,
            collection_id,
            drafted,
            actor=actor,
            origin=origin,
            user_id=user_id,
            session_id=session_id,
            source_refs=source_refs,
        )

    async def _submit_draft(
        self,
        namespace_id: uuid.UUID,
        collection_id: uuid.UUID,
        drafted: tuple[DraftReply, Provenance],
        *,
        actor: Actor | None,
        origin: KbOrigin,
        user_id: str | None,
        session_id: str | None,
        source_refs: list[dict[str, Any]] | None,
    ) -> Submitted:
        draft, provenance = drafted
        return await self.submit(
            namespace_id,
            collection_id,
            draft.title,
            draft.content,
            actor=actor or Actor(ActorType.AI, provenance.tag),
            risk_level=RiskLevel(draft.risk_level),
            user_id=user_id,
            session_id=session_id,
            origin=origin,
            author_type=AuthorType.AI,
            source_refs=source_refs,
            reason=f"drafted by {provenance.tag}: {draft.reason}",
            labelled=True,
        )

    async def promote(
        self,
        namespace_id: uuid.UUID,
        memory_id: uuid.UUID,
        collection_id: uuid.UUID,
        *,
        actor: Actor,
        user_id: str | None = None,
    ) -> Submitted:
        """A memory, rewritten as general knowledge (design §7 promote). Its owner is asked unless
        another `user_id` is given."""
        async with self._sessions() as s:
            memory = await load_owned(s, Memory, namespace_id, memory_id, "memory", lock=False)
        if memory.status != MemoryStatus.ACTIVE:
            raise Unprocessable(f"a {memory.status.value!r} memory cannot be promoted")
        return await self._drafted(
            namespace_id,
            collection_id,
            memory.content,
            actor=actor,
            origin=KbOrigin.PROMOTED_MEMORY,
            user_id=user_id or memory.user_id,
            session_id=memory.session_id,
            source_refs=[{"type": "memory", "id": str(memory.id)}],
        )

    async def suggest(
        self,
        namespace_id: uuid.UUID,
        collection_id: uuid.UUID,
        text: str,
        *,
        actor: Actor,
        user_id: str | None = None,
        session_id: str | None = None,
    ) -> Submitted:
        """The host app proposes saving `text` (design §7 suggestions): drafted, then routed."""
        return await self._drafted(
            namespace_id,
            collection_id,
            text,
            actor=actor,
            origin=KbOrigin.USER_SUGGESTION,
            user_id=user_id,
            session_id=session_id,
        )

    # --- worthiness after extraction (design §5 steps 5-6) ------------------------------------

    async def queue_suggestions(self, namespace_id: uuid.UUID, memory_ids: list[uuid.UUID]) -> None:
        """After extraction: score the new memories in a job of their own (outbox), so a KB LLM
        failure retries by itself and never holds up memory extraction."""
        if self._llm is None or not memory_ids:
            return
        async with self._sessions() as s, s.begin():
            enqueue(
                s, namespace_id, JobType.KB_SUGGEST, {"memory_ids": [str(i) for i in memory_ids]}
            )

    async def handle(self, job: ClaimedJob) -> int:
        ids = [uuid.UUID(i) for i in job.payload["memory_ids"]]
        return await self.suggest_from_memories(job.namespace_id, ids)

    async def suggest_from_memories(
        self, namespace_id: uuid.UUID, memory_ids: list[uuid.UUID]
    ) -> int:
        """Score the memories in one call; draft the worthy ones (concurrently, within each
        session's card budget) and submit them. Returns how many were submitted.

        Each memory is scored once: they are marked (`meta.kb_scored`, one transaction) before
        any drafting, so a job that fails halfway and retries never makes the same card twice;
        it can lose those suggestions instead, which only costs a card nobody saw."""
        llm = self._drafting()
        async with self._sessions() as s:
            config = (await read_config(s, namespace_id)).kb
            memories = [
                m
                for m in await s.scalars(
                    select(Memory)
                    .where(Memory.namespace_id == namespace_id, Memory.id.in_(memory_ids))
                    .order_by(Memory.id)
                )
                if m.status == MemoryStatus.ACTIVE and not m.meta.get(SCORED)
            ]
            stopped = await _stopped(s, namespace_id, {m.user_id for m in memories if m.user_id})
        if not memories:
            return 0
        await self._governance.apply_all(
            [PatchMemory(namespace_id, m.id, meta={**m.meta, SCORED: True}) for m in memories],
            SYSTEM,
        )
        memories = [m for m in memories if m.user_id not in stopped]  # never scored: no card
        if not memories:
            return 0
        scores, scored_by = await score_memories(llm, [(str(m.id), m.content) for m in memories])
        worthy = set(
            worthy_ids(scores, {str(m.id) for m in memories}, threshold=config.suggest_threshold)
        )
        chosen = await self._within_budget(
            namespace_id,
            [m for m in memories if str(m.id) in worthy],
            config.max_suggestions_per_session,
        )
        if not chosen:
            return 0
        slots = asyncio.Semaphore(DRAFTS_AT_ONCE)

        async def draft(memory: Memory) -> tuple[DraftReply, Provenance]:
            async with slots:
                return await draft_entry(llm, memory.content)

        drafts = await asyncio.gather(*(draft(m) for m in chosen))
        collection = await self._suggest_collection(namespace_id, config.suggest_collection)
        for memory, drafted in zip(chosen, drafts, strict=True):
            refs = [{"type": "memory", "id": str(memory.id)}]
            if scored_by is not None:
                refs.append({"type": "llm", "id": scored_by.tag})
            await self._submit_draft(
                namespace_id,
                collection,
                drafted,
                actor=None,
                origin=KbOrigin.EXTRACTION,
                user_id=memory.user_id,
                session_id=memory.session_id,
                source_refs=refs,
            )
        return len(chosen)

    async def _within_budget(
        self, namespace_id: uuid.UUID, memories: list[Memory], per_session: int
    ) -> list[Memory]:
        """Do not disturb (design §5): per user and session, no more cards than the budget left.
        Decided before drafting, so no draft is paid for and then dropped."""
        left: dict[tuple[str | None, str | None], int] = {}
        out = []
        for memory in memories:
            if memory.user_id is None:  # nobody to ask: no card, no budget
                out.append(memory)
                continue
            key = (memory.user_id, memory.session_id)
            if key not in left:
                left[key] = per_session - await self._count_open_cards(namespace_id, *key)
            if left[key] > 0:
                left[key] -= 1
                out.append(memory)
        return out

    async def _count_open_cards(
        self, namespace_id: uuid.UUID, user_id: str | None, session_id: str | None
    ) -> int:
        if user_id is None:
            return 0
        async with self._sessions() as s:
            query = select(func.count()).where(*_open_cards(namespace_id, user_id, session_id))
            return int(await s.scalar(query) or 0)

    async def suggestion_collection(self, namespace_id: uuid.UUID) -> uuid.UUID:
        """The namespace's collection for suggested knowledge (`kb.suggest_collection`),
        created on first use."""
        async with self._sessions() as s:
            name = (await read_config(s, namespace_id)).kb.suggest_collection
        return await self._suggest_collection(namespace_id, name)

    async def get_candidate(self, namespace_id: uuid.UUID, candidate_id: uuid.UUID) -> Candidate:
        async with self._sessions() as s:
            return await load_owned(
                s, Candidate, namespace_id, candidate_id, "candidate", lock=False
            )

    async def _suggest_collection(self, namespace_id: uuid.UUID, name: str) -> uuid.UUID:
        async with self._sessions() as s:
            found = await s.scalar(
                select(KbCollection.id).where(
                    KbCollection.namespace_id == namespace_id, KbCollection.name == name
                )
            )
        if found is not None:
            return found
        created: KbCollection = await self._governance.apply(
            CreateCollection(namespace_id, name), SYSTEM
        )
        return created.id

    async def cards(self, scope: Scope) -> list[Candidate]:
        """The end user's open cards in this session (design §5), newest first: shown on episode
        and retrieve responses. The per-session budget limits how many the pipeline makes; a host
        app's own suggestions are its decision."""
        if scope.user_id is None:
            return []
        async with self._sessions() as s:
            rows = await s.scalars(
                select(Candidate)
                .where(*_open_cards(scope.namespace_id, scope.user_id, scope.session_id))
                .order_by(Candidate.id.desc())
            )
            return list(rows)

    async def revise(
        self,
        namespace_id: uuid.UUID,
        entry_id: uuid.UUID,
        *,
        actor: Actor,
        title: str | None = None,
        content: str | None = None,
    ) -> Submitted:
        floor, notes = await self._label(title or "", content or "", RiskLevel.LOW)
        op = ReviseKbEntry(
            namespace_id,
            entry_id,
            title=title,
            content=content,
            risk_level=floor,
            reason="; ".join(notes) or None,
        )
        return Submitted.of(await self._governance.apply(op, actor))

    async def list_collections(self, namespace_id: uuid.UUID) -> list[KbCollection]:
        async with self._sessions() as s:
            rows = await s.scalars(
                select(KbCollection)
                .where(KbCollection.namespace_id == namespace_id)
                .order_by(KbCollection.id)
            )
            return list(rows)

    async def get_entry(self, namespace_id: uuid.UUID, entry_id: uuid.UUID) -> KbEntry:
        async with self._sessions() as s:
            return await load_owned(s, KbEntry, namespace_id, entry_id, "KB entry", lock=False)

    async def list_entries(
        self, namespace_id: uuid.UUID, filters: EntryFilter, *, cursor: uuid.UUID | None, limit: int
    ) -> tuple[list[KbEntry], uuid.UUID | None]:
        query = select(KbEntry).where(KbEntry.namespace_id == namespace_id)
        if filters.collection_id is not None:
            query = query.where(KbEntry.collection_id == filters.collection_id)
        if filters.status is not None:
            query = query.where(KbEntry.status == filters.status)
        if filters.review == "due":
            query = query.where(KbEntry.review_due_at <= utcnow())  # retiring clears it
        elif filters.review == "verify":
            rated = KbEntry.helpful_count + KbEntry.unhelpful_count
            query = query.where(
                KbEntry.status == KbStatus.ACTIVE,
                KbEntry.helpful_count >= VERIFY_MIN_HELPFUL,
                _share_at_least(KbEntry.helpful_count, rated, VERIFY_HELPFUL_PERCENT),
            )
        async with self._sessions() as s:
            return await page(s, query, KbEntry, cursor, limit)

    async def flag_for_review(self) -> int:
        """Flag effective knowledge often rated unhelpful since its last review (a daily worker
        tick, every namespace), each in its own transaction; returns how many were flagged."""
        unhelpful, rated = KbEntry.unhelpful_since_review, KbEntry.rated_since_review
        async with self._sessions() as s:
            due = (
                await s.execute(
                    select(KbEntry.namespace_id, KbEntry.id).where(
                        KbEntry.status.in_(EFFECTIVE_KB_STATUSES),
                        KbEntry.review_due_at.is_(None),
                        KbEntry.hits_since_review >= REVIEW_MIN_HITS,
                        rated > 0,
                        _share_at_least(unhelpful, rated, REVIEW_UNHELPFUL_PERCENT),
                    )
                )
            ).all()
        flagged = 0
        for namespace_id, entry_id in due:
            try:
                await self._governance.apply(FlagKbEntryForReview(namespace_id, entry_id), SYSTEM)
            except NotFound:
                continue  # gone between the scan and now (erasure): not the whole sweep
            flagged += 1
        return flagged

    async def list_candidates(
        self,
        namespace_id: uuid.UUID,
        filters: CandidateFilter,
        *,
        cursor: uuid.UUID | None,
        limit: int,
    ) -> tuple[list[Candidate], uuid.UUID | None]:
        query = select(Candidate).where(Candidate.namespace_id == namespace_id)
        for column, value in (
            (Candidate.stage, filters.stage),
            (Candidate.status, filters.status),
            (Candidate.requested_for_user, filters.user_id),
        ):
            if value is not None:
                query = query.where(column == value)
        async with self._sessions() as s:
            return await page(s, query, Candidate, cursor, limit)

    async def decide(
        self,
        namespace_id: uuid.UUID,
        candidate_id: uuid.UUID,
        to: CandidateStatus,
        actor: Actor,
        *,
        draft: dict[str, Any] | None = None,
        scope: Literal["personal", "team"] = "team",
        note: str | None = None,
        stop_suggestions: bool = False,
    ) -> tuple[Candidate, KbEntry | None]:
        """The decided candidate, and the knowledge it became (if it became any). An edited draft
        gets the KB LLM's risk label too (governance re-runs the rules). `stop_suggestions` (a
        rejection only): no more automatic cards for the card's user (design §5)."""
        rule = None
        if stop_suggestions:
            if to != CandidateStatus.REJECTED:
                raise Unprocessable("stop_suggestions goes with a rejection")
            rule = await self._stop_rule(namespace_id, candidate_id)
        floor: RiskLevel | None = None
        if draft and to == CandidateStatus.ACCEPTED:
            floor, _ = await self._label(
                draft.get("title", ""), draft.get("content", ""), RiskLevel.LOW
            )
        op = DecideCandidate(
            namespace_id,
            candidate_id,
            to,
            note=note,
            draft=draft,
            scope=scope,
            risk_level=floor,
        )
        ops: list[Operation] = [op] if rule is None else [op, rule]  # one transaction
        candidate: Candidate = (await self._governance.apply_all(ops, actor))[0]
        if candidate.status != CandidateStatus.ACCEPTED or candidate.target_id is None:
            return candidate, None
        async with self._sessions() as s:  # accepting points target_id at the knowledge
            return candidate, await s.get(KbEntry, candidate.target_id)

    async def _stop_rule(self, namespace_id: uuid.UUID, candidate_id: uuid.UUID) -> CreateMemory:
        """The card's user's "stop suggesting", as a procedural memory they can see and delete
        (asked twice, the newer supersedes the older). A card asked of no user has nothing to
        stop."""
        async with self._sessions() as s:
            card = await load_owned(
                s, Candidate, namespace_id, candidate_id, "candidate", lock=False
            )
            user = card.requested_for_user
            if user is None:
                raise Unprocessable("this card was not asked of a user: no suggestions to stop")
        return CreateMemory(  # the profile key keeps one active rule per user, even under a race
            namespace_id,
            "Does not want the assistant to suggest sharing what they say with the team",
            user_id=user,
            memory_type=PROCEDURAL,
            key=STOP_KEY,
            internal=True,
        )


async def _stopped(s: AsyncSession, namespace_id: uuid.UUID, users: set[str]) -> set[str]:
    """Which of these users asked for no more suggestions (an active rule)."""
    if not users:
        return set()
    found = await s.scalars(
        select(Memory.user_id).where(
            Memory.namespace_id == namespace_id,
            Memory.user_id.in_(users),
            Memory.key == STOP_KEY,
            Memory.memory_type == PROCEDURAL,  # with the reserved key: belt and braces
            Memory.status == MemoryStatus.ACTIVE,
        )
    )
    return {user for user in found if user is not None}


def _open_cards(namespace_id: uuid.UUID, user_id: str, session_id: str | None) -> tuple[Any, ...]:
    session = (
        Candidate.session_id.is_(None) if session_id is None else Candidate.session_id == session_id
    )
    return (
        Candidate.namespace_id == namespace_id,
        Candidate.requested_for_user == user_id,
        session,
        Candidate.stage == CandidateStage.AWAITING_USER,
        Candidate.status == CandidateStatus.PENDING,
    )
