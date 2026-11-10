"""KB governance (design §5, P3): who may verify, routing by risk and review policy, and what an
accepted candidate becomes. Service level, both dialects; the HTTP layer is in test_kb_api.py."""

import uuid
from datetime import timedelta
from typing import Any

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from llm_steward.errors import Conflict, Forbidden, Unprocessable
from llm_steward.models import Candidate, ChangeLog, Job, KbEntry, Memory
from llm_steward.models.base import utcnow
from llm_steward.models.enums import (
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
    RiskLevel,
)
from llm_steward.services.governance_service import (
    SYSTEM,
    Actor,
    CreateCandidate,
    CreateCollection,
    DecideCandidate,
    GovernanceService,
    ReviseKbEntry,
    SubmitKbDocument,
    SubmitKbEntry,
    TransitionKbEntry,
    UpdateCollection,
)
from tests.conftest import REVIEWER, governance, kb_entry_op

HOST = Actor(ActorType.HOST_APP, "api_key:host")
ALICE = Actor(ActorType.END_USER, "alice")


class KbStack:
    def __init__(
        self, sessions: async_sessionmaker[AsyncSession], gov: GovernanceService, ns: uuid.UUID
    ) -> None:
        self.sessions, self.gov, self.ns = sessions, gov, ns

    async def collection(self, **kw: Any) -> Any:
        name = f"c-{uuid.uuid4().hex[:6]}"
        return await self.gov.apply(CreateCollection(self.ns, name, **kw), SYSTEM)

    async def submit(
        self, risk: RiskLevel, actor: Actor = HOST, *, user: str | None = None, **kw: Any
    ) -> Any:
        collection = kw.pop("collection", None) or await self.collection(default_risk=RiskLevel.LOW)
        op = SubmitKbEntry(
            namespace_id=self.ns,
            collection_id=collection.id,
            title=kw.pop("title", "Refund time"),
            content=kw.pop("content", "Refunds take 3 to 5 business days"),
            risk_level=risk,
            origin=KbOrigin.MANUAL,
            author_type=kw.pop("author_type", AuthorType.HUMAN),
            requested_for_user=user,
            **kw,
        )
        return await self.gov.apply(op, actor)

    async def decide(self, candidate: Any, actor: Actor, to: CandidateStatus, **kw: Any) -> Any:
        return await self.gov.apply(DecideCandidate(self.ns, candidate.id, to, **kw), actor)

    async def jobs(self) -> list[JobType]:
        async with self.sessions() as s:
            return [JobType(t) for t in await s.scalars(select(Job.type).order_by(Job.created_at))]

    async def count(self, model: Any) -> int:
        async with self.sessions() as s:
            return int(await s.scalar(select(func.count()).select_from(model)) or 0)

    async def entries(self) -> list[KbEntry]:
        async with self.sessions() as s:
            return list(await s.scalars(select(KbEntry).order_by(KbEntry.created_at)))


@pytest.fixture
def kb(sessions: async_sessionmaker[AsyncSession], namespace_id: uuid.UUID) -> KbStack:
    return KbStack(sessions, governance(sessions), namespace_id)


# --- who may verify -----------------------------------------------------------------------------


NOT_REVIEWERS = [
    HOST,
    ALICE,
    Actor(ActorType.AI, "kb.draft:model"),
    SYSTEM,
    Actor(ActorType.PROVIDER, "mem0"),
]


@pytest.mark.parametrize("actor", NOT_REVIEWERS, ids=lambda a: a.type.value)
async def test_only_a_reviewer_can_create_or_make_an_entry_verified(
    kb: KbStack, actor: Actor
) -> None:
    collection = await kb.collection()
    with pytest.raises(Forbidden, match="reviewer"):
        await kb.gov.apply(kb_entry_op(kb.ns, collection.id, KbStatus.VERIFIED), actor)
    draft = await kb.gov.apply(kb_entry_op(kb.ns, collection.id, KbStatus.DRAFT), SYSTEM)
    with pytest.raises(Forbidden, match="reviewer"):
        await kb.gov.apply(TransitionKbEntry(kb.ns, draft.id, KbStatus.VERIFIED), actor)
    verified = await kb.gov.apply(TransitionKbEntry(kb.ns, draft.id, KbStatus.VERIFIED), REVIEWER)
    assert (verified.status, verified.verified_by) == (KbStatus.VERIFIED, REVIEWER.id)


# --- routing by risk (design §5 review policy) ----------------------------------------------------


async def test_low_risk_goes_live_at_once(kb: KbStack) -> None:
    entry = await kb.submit(RiskLevel.LOW)
    assert isinstance(entry, KbEntry) and entry.status == KbStatus.ACTIVE
    assert await kb.jobs() == [JobType.KB_UPSERT]


async def test_medium_risk_asks_the_end_user_when_there_is_one(kb: KbStack) -> None:
    asked = await kb.submit(RiskLevel.MEDIUM, user="alice")
    assert isinstance(asked, Candidate)
    assert (asked.stage, asked.status, asked.requested_for_user) == (
        CandidateStage.AWAITING_USER,
        CandidateStatus.PENDING,
        "alice",
    )
    assert asked.expires_at is not None and asked.expires_at > utcnow() + timedelta(days=6)
    assert await kb.count(KbEntry) == 0 and await kb.jobs() == []

    no_user = await kb.submit(RiskLevel.MEDIUM)
    assert isinstance(no_user, KbEntry) and no_user.status == KbStatus.ACTIVE


async def test_high_risk_waits_for_a_reviewer_and_never_reaches_the_provider(kb: KbStack) -> None:
    for user in (None, "alice"):
        candidate = await kb.submit(RiskLevel.HIGH, user=user)
        assert isinstance(candidate, Candidate)
        assert candidate.stage == CandidateStage.AWAITING_REVIEW
    assert await kb.count(KbEntry) == 0 and await kb.jobs() == []


async def test_a_reviewer_submitting_creates_verified_knowledge(kb: KbStack) -> None:
    entry = await kb.submit(RiskLevel.HIGH, REVIEWER)
    assert isinstance(entry, KbEntry) and entry.status == KbStatus.VERIFIED
    assert entry.verified_by == REVIEWER.id


async def test_a_proposer_only_ever_proposes(kb: KbStack) -> None:
    """An actor who may only propose (an agent over MCP, rule 5): low risk, a human author, a
    reviewer's own key, an end user to ask — still a candidate in the review queue, and it can
    decide nothing, not even the candidate it made."""
    from dataclasses import replace

    for actor, user in ((REVIEWER, None), (HOST, "alice"), (HOST, None)):
        proposer = replace(actor, propose_only=True)
        assert not proposer.is_reviewer
        proposed = await kb.submit(RiskLevel.LOW, proposer, user=user)
        assert isinstance(proposed, Candidate)
        assert proposed.stage == CandidateStage.AWAITING_REVIEW
        with pytest.raises(Forbidden):
            await kb.decide(proposed, proposer, CandidateStatus.ACCEPTED)
    assert await kb.count(KbEntry) == 0 and await kb.jobs() == []
    live = await kb.submit(RiskLevel.LOW)  # the host's own knowledge
    revision = ReviseKbEntry(kb.ns, live.id, content="Refunds take a day")
    waiting = await kb.gov.apply(revision, replace(REVIEWER, propose_only=True))
    assert isinstance(waiting, Candidate) and waiting.stage == CandidateStage.AWAITING_REVIEW
    collection = await kb.collection(default_risk=RiskLevel.LOW)
    upload = SubmitKbDocument(
        kb.ns, collection.id, "Policy", "policy.md", "text/markdown", b"# Policy", "digest"
    )
    document, card = await kb.gov.apply(upload, replace(REVIEWER, propose_only=True))
    assert document.state == KbStatus.DRAFT and card is not None  # not live, not verified
    asked = await kb.submit(RiskLevel.MEDIUM, user="alice")  # a card for alice
    alice = Actor(ActorType.END_USER, "alice", propose_only=True)  # alice's agent, over MCP
    with pytest.raises(Forbidden):
        await kb.decide(asked, alice, CandidateStatus.ACCEPTED)


async def test_the_collection_default_risk_is_a_floor(kb: KbStack) -> None:
    strict = await kb.collection(default_risk=RiskLevel.HIGH)
    candidate = await kb.submit(RiskLevel.LOW, collection=strict)
    assert isinstance(candidate, Candidate) and candidate.risk_level == RiskLevel.HIGH


async def test_a_collection_policy_can_make_review_stricter_but_never_looser(kb: KbStack) -> None:
    strict = await kb.collection(default_risk=RiskLevel.LOW)
    async with kb.sessions() as s, s.begin():
        row = await s.get(type(strict), strict.id)
        assert row is not None
        row.review_policy = {"low": "review", "high": "auto"}  # high cannot be loosened
    reviewed = await kb.submit(RiskLevel.LOW, collection=strict)
    assert isinstance(reviewed, Candidate) and reviewed.stage == CandidateStage.AWAITING_REVIEW
    high = await kb.submit(RiskLevel.HIGH, collection=strict)
    assert isinstance(high, Candidate) and high.stage == CandidateStage.AWAITING_REVIEW


# --- deciding a candidate -------------------------------------------------------------------------


async def test_an_end_user_accepting_for_the_team_makes_it_active_never_verified(
    kb: KbStack,
) -> None:
    candidate = await kb.submit(RiskLevel.MEDIUM, user="alice")
    decided = await kb.decide(candidate, ALICE, CandidateStatus.ACCEPTED, scope="team")
    assert decided.status == CandidateStatus.ACCEPTED
    [entry] = await kb.entries()
    assert entry.status == KbStatus.ACTIVE and entry.verified_by is None
    assert {"type": "candidate", "id": str(candidate.id)} in entry.source_refs
    assert await kb.jobs() == [JobType.KB_UPSERT]


async def test_an_end_user_accepting_for_themselves_makes_a_memory_not_knowledge(
    kb: KbStack,
) -> None:
    candidate = await kb.submit(RiskLevel.MEDIUM, user="alice")
    await kb.decide(candidate, ALICE, CandidateStatus.ACCEPTED, scope="personal")
    assert await kb.count(KbEntry) == 0
    async with kb.sessions() as s:
        memory = (await s.scalars(select(Memory))).one()
    assert (memory.user_id, memory.content) == ("alice", "Refunds take 3 to 5 business days")


async def test_a_high_risk_candidate_accepted_by_the_user_still_needs_a_reviewer(
    kb: KbStack,
) -> None:
    async with kb.sessions() as s, s.begin():  # a high-risk candidate that asks the user first
        candidate = Candidate(
            namespace_id=kb.ns,
            target_type=CandidateTarget.KB_ENTRY,
            op=CandidateOp.ADD,
            draft={"collection_id": str((await kb.collection()).id), "title": "T", "content": "C"},
            risk_level=RiskLevel.HIGH,
            origin=CandidateOrigin.EXTRACTION,
            stage=CandidateStage.AWAITING_USER,
            status=CandidateStatus.PENDING,
            requested_for_user="alice",
        )
        s.add(candidate)
    moved = await kb.decide(candidate, ALICE, CandidateStatus.ACCEPTED, scope="team")
    assert (moved.stage, moved.status) == (CandidateStage.AWAITING_REVIEW, CandidateStatus.PENDING)
    assert await kb.count(KbEntry) == 0 and await kb.jobs() == []


async def test_only_a_reviewer_can_accept_the_review_queue(kb: KbStack) -> None:
    candidate = await kb.submit(RiskLevel.HIGH, user="alice")
    for actor in (ALICE, HOST):
        for to in (CandidateStatus.ACCEPTED, CandidateStatus.REJECTED):  # nor reject it
            with pytest.raises(Forbidden, match="reviewer"):
                await kb.decide(candidate, actor, to)
    edited = {"title": "Refund time", "content": "Refunds take 7 business days"}
    await kb.decide(candidate, REVIEWER, CandidateStatus.ACCEPTED, draft=edited)
    [entry] = await kb.entries()
    assert (entry.status, entry.verified_by, entry.content) == (
        KbStatus.VERIFIED,
        REVIEWER.id,
        "Refunds take 7 business days",
    )


async def test_only_the_asked_user_can_answer(kb: KbStack) -> None:
    candidate = await kb.submit(RiskLevel.MEDIUM, user="alice")
    with pytest.raises(Forbidden, match="alice"):
        await kb.decide(candidate, Actor(ActorType.END_USER, "bob"), CandidateStatus.ACCEPTED)


async def test_a_rejected_candidate_creates_nothing(kb: KbStack) -> None:
    candidate = await kb.submit(RiskLevel.HIGH)
    rejected = await kb.decide(candidate, REVIEWER, CandidateStatus.REJECTED, note="not true")
    assert rejected.status == CandidateStatus.REJECTED
    assert await kb.count(KbEntry) == 0 and await kb.jobs() == []


async def test_an_expired_candidate_cannot_be_accepted(kb: KbStack) -> None:
    collection = await kb.collection()
    candidate = await kb.gov.apply(
        CreateCandidate(
            namespace_id=kb.ns,
            target_type=CandidateTarget.KB_ENTRY,
            op=CandidateOp.ADD,
            draft={"collection_id": str(collection.id), "title": "T", "content": "C"},
            risk_level=RiskLevel.MEDIUM,
            origin=CandidateOrigin.USER_SUGGESTION,
            stage=CandidateStage.AWAITING_USER,
            requested_for_user="alice",
            expires_at=utcnow() - timedelta(minutes=1),
        ),
        SYSTEM,
    )
    with pytest.raises(Conflict, match="expired"):
        await kb.decide(candidate, ALICE, CandidateStatus.ACCEPTED, scope="team")
    assert await kb.count(KbEntry) == 0


async def test_update_and_delete_candidates_change_the_existing_entry(kb: KbStack) -> None:
    entry = await kb.submit(RiskLevel.LOW)
    common = {
        "namespace_id": kb.ns,
        "target_type": CandidateTarget.KB_ENTRY,
        "target_id": entry.id,
        "risk_level": RiskLevel.HIGH,
        "origin": CandidateOrigin.API,
        "stage": CandidateStage.AWAITING_REVIEW,
    }
    update = await kb.gov.apply(
        CreateCandidate(op=CandidateOp.UPDATE, draft={"content": "Now 7 days"}, **common), SYSTEM
    )
    await kb.decide(update, REVIEWER, CandidateStatus.ACCEPTED)
    [updated] = await kb.entries()
    assert (updated.content, updated.version, updated.status) == (
        "Now 7 days",
        2,
        KbStatus.VERIFIED,  # a reviewer approved this version
    )
    delete = await kb.gov.apply(CreateCandidate(op=CandidateOp.DELETE, draft={}, **common), SYSTEM)
    await kb.decide(delete, REVIEWER, CandidateStatus.ACCEPTED)
    [retired] = await kb.entries()
    assert retired.status == KbStatus.DEPRECATED
    assert (await kb.jobs())[-1] == JobType.KB_REMOVE


# --- collections and revisions ------------------------------------------------------------------


async def test_a_collection_policy_is_set_and_changed_with_a_log(kb: KbStack) -> None:
    collection = await kb.collection(review_policy={"low": "review"})
    assert collection.review_policy == {"low": "review"}
    changed = await kb.gov.apply(
        UpdateCollection(kb.ns, collection.id, default_risk=RiskLevel.HIGH, review_policy={}),
        REVIEWER,
    )
    assert (changed.default_risk, changed.review_policy) == (RiskLevel.HIGH, {})
    async with kb.sessions() as s:
        logged = await s.scalar(
            select(func.count()).select_from(ChangeLog).where(ChangeLog.object_id == collection.id)
        )
    assert logged == 2
    with pytest.raises(Unprocessable, match="review_policy"):
        await kb.gov.apply(
            UpdateCollection(kb.ns, collection.id, review_policy={"high": "auto"}), REVIEWER
        )


async def test_a_revision_of_ordinary_knowledge_applies_at_once(kb: KbStack) -> None:
    entry = await kb.submit(RiskLevel.LOW)
    revised = await kb.gov.apply(
        ReviseKbEntry(kb.ns, entry.id, content="Refunds take 4 days", risk_level=RiskLevel.LOW),
        HOST,
    )
    assert isinstance(revised, KbEntry) and (revised.version, revised.status) == (
        2,
        KbStatus.ACTIVE,
    )


@pytest.mark.parametrize("verified", [True, False], ids=["verified", "high-risk-edit"])
async def test_a_revision_of_verified_or_risky_knowledge_waits_for_a_reviewer(
    kb: KbStack, verified: bool
) -> None:
    entry = await kb.submit(RiskLevel.LOW, REVIEWER if verified else HOST)
    risk = RiskLevel.LOW if verified else RiskLevel.HIGH
    revision = ReviseKbEntry(kb.ns, entry.id, content="Refunds take 30 days", risk_level=risk)
    candidate = await kb.gov.apply(revision, HOST)
    assert isinstance(candidate, Candidate)
    assert (candidate.op, candidate.target_id, candidate.stage) == (
        CandidateOp.UPDATE,
        entry.id,
        CandidateStage.AWAITING_REVIEW,
    )
    [unchanged] = await kb.entries()
    assert (unchanged.content, unchanged.version) == ("Refunds take 3 to 5 business days", 1)
    # a reviewer's own revision applies directly, and keeps it verified
    applied = await kb.gov.apply(revision, REVIEWER)
    assert isinstance(applied, KbEntry) and applied.content == "Refunds take 30 days"


async def test_a_decision_can_only_edit_title_and_content(kb: KbStack) -> None:
    candidate = await kb.submit(RiskLevel.HIGH)
    other = await kb.collection()
    with pytest.raises(Unprocessable, match="collection_id"):
        await kb.decide(
            candidate, REVIEWER, CandidateStatus.ACCEPTED, draft={"collection_id": str(other.id)}
        )


async def test_a_revision_follows_the_collection_policy_like_a_submission(kb: KbStack) -> None:
    strict = await kb.collection(default_risk=RiskLevel.LOW, review_policy={"low": "review"})
    entry = await kb.gov.apply(kb_entry_op(kb.ns, strict.id, KbStatus.ACTIVE), SYSTEM)
    revision = await kb.gov.apply(ReviseKbEntry(kb.ns, entry.id, content="Now 9 days"), HOST)
    assert isinstance(revision, Candidate) and revision.stage == CandidateStage.AWAITING_REVIEW


async def test_a_submission_is_screened_by_governance_itself(kb: KbStack) -> None:
    """Whoever calls apply (the API, the pipeline, MCP) gets the same rules."""
    candidate = await kb.submit(RiskLevel.LOW, content="Mail alice@example.com")
    assert isinstance(candidate, Candidate)
    assert (candidate.risk_level, candidate.draft["content"]) == (
        RiskLevel.HIGH,
        "Mail [REDACTED:email]",
    )


# --- AI-authored knowledge, and what is written about it --------------------------------------


@pytest.mark.parametrize(("user", "stage"), [(None, "review"), ("alice", "user")])
async def test_ai_authored_knowledge_never_goes_live_without_a_person(
    kb: KbStack, user: str | None, stage: str
) -> None:
    """A model that rewrote untrusted text also labels its risk: a prompt injection could make
    it say "low". So a person always sees it: the asked user, or a reviewer."""
    candidate = await kb.submit(
        RiskLevel.LOW, Actor(ActorType.AI, "kb.draft:m"), user=user, author_type=AuthorType.AI
    )
    assert isinstance(candidate, Candidate)
    assert candidate.stage == (
        CandidateStage.AWAITING_REVIEW if stage == "review" else CandidateStage.AWAITING_USER
    )
    assert await kb.count(KbEntry) == 0


async def test_reasons_and_notes_are_redacted_like_the_text(kb: KbStack) -> None:
    candidate = await kb.submit(RiskLevel.HIGH, reason="model says: ask alice@example.com")
    assert candidate.reason == "model says: ask [REDACTED:email]"
    decided = await kb.decide(
        candidate, REVIEWER, CandidateStatus.REJECTED, note="call 13812345678 first"
    )
    assert decided.decision_note == "call [REDACTED:phone] first"


TAKERS_OUT = [a for a in NOT_REVIEWERS if a != SYSTEM]  # SYSTEM: llm-steward's own maintenance


@pytest.mark.parametrize("actor", TAKERS_OUT, ids=lambda a: a.type.value)
async def test_only_a_reviewer_can_take_knowledge_out(kb: KbStack, actor: Actor) -> None:
    """Enforced in governance, not only by the API's review scope: every caller of apply (the
    pipeline, MCP later) is held to it."""
    entry = await kb.submit(RiskLevel.LOW)
    with pytest.raises(Forbidden):
        await kb.gov.apply(TransitionKbEntry(kb.ns, entry.id, KbStatus.DEPRECATED), actor)
    [still] = await kb.entries()
    assert still.status == KbStatus.ACTIVE
    retired = await kb.gov.apply(TransitionKbEntry(kb.ns, entry.id, KbStatus.DEPRECATED), REVIEWER)
    assert retired.status == KbStatus.DEPRECATED


async def test_every_reason_written_to_history_is_redacted(kb: KbStack) -> None:
    """Free text in change_log is redacted where it is written (`_log`), whatever op carried it."""
    entry = await kb.submit(RiskLevel.LOW)
    reason = "asked by bob@example.com on +86 138 0013 8000"
    await kb.gov.apply(
        TransitionKbEntry(kb.ns, entry.id, KbStatus.DEPRECATED, reason=reason), REVIEWER
    )
    async with kb.sessions() as s:
        logged = await s.scalar(
            select(ChangeLog.reason).where(
                ChangeLog.object_id == entry.id, ChangeLog.op == ChangeOp.TRANSITION
            )
        )
    assert logged is not None
    assert logged == "asked by [REDACTED:email] on [REDACTED:phone]"
